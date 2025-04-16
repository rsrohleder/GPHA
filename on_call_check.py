import time
import json
import schedule
import logging
import requests
import msal
from datetime import datetime, timedelta
from datetime import date
from twilio.rest import Client
from dotenv import load_dotenv
import os
import sqlite3
from contextlib import contextmanager

# Configure Logging
logging.basicConfig(
    filename="/home/gphalx/Apps/Oncall/logs/oncallapp.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Load environment variables from .env file
load_dotenv()

# Azure Authentication Config
CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
AUTHORITY = "https://login.microsoftonline.com/dae4f315-a0df-4ea5-9f07-ac5f8390bac3"
SCOPE = ["https://graph.microsoft.com/.default"]

# Twilio Config
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM = "+18777131918"
twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)

# Solarwinds Config
SOLARWINDS_TOKEN = os.getenv("SOLARWINDS_API_TOKEN")

# Database path
DB_PATH = "/home/gphalx/Apps/Oncall/oncall.db"

# Contact information dictionary
CONTACTS = {
    "Ashley": ["+17852591850", "aedwards@gpha.com"],
    "Chelsea": ["+17856560283", "ceickhoff@gpha.com"],
    "Colton": ["+17856391310", "cscoby@gpha.com"],
    "Dan": ["+17852592308", "dcochran@gpha.com"],
    "David": ["+17852012682", "darellano@gpha.com"],
    "Freddi": ["+17852590310", "wunruh@gpha.com"],
    "Jacoby": ["+17855349811", "jjensen@gpha.com"],
    "Jaime": ["+17857373847", "jgosselin@gpha.com"],
    "Kaden": ["+17853020569", "krohr@gpha.com"],
    "Leah": ["+17852597944", "lcochran@gpha.com"],
    "Lexi": ["+17856397987", "srohleder@gpha.com"],
    "Lisa": ["+17856585979", "lgier@gpha.com"],
    "Nick": ["+17856560119", "nsells@gpha.com"],
    "Patrick": ["+17852595392", "phertel@gpha.com"],
    "Renae": ["+17856561587", "rwellbrock@gpha.com"],
    "Sadee": ["+17855437078", "ssoldan@gpha.com"],
    "Shelby": ["+17852596787", "sberry@gpha.com"],
    "Stephanie": ["+17852596026", "sbanker@gpha.com"],
    "Tammy": ["+17852596642", "tkrause@gpha.com"],
    "Trina": ["+17852595596", "tyauch@gpha.com"],
    "Vivian": ["+17852594729", "vdietz@gpha.com"],
    "Viv": ["+17852594729", "vdietz@gpha.com"]
}


@contextmanager
def get_db_connection():
    """Context manager for SQLite database connections."""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        yield conn
    except sqlite3.Error as e:
        logging.error(f"Database error: {e}")
        raise
    finally:
        if conn:
            conn.commit()
            conn.close()


def initialize_database():
    """Initialize the database if it doesn't exist."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    messageID TEXT PRIMARY KEY,
                    ticketID TEXT
                )
            ''')
        logging.info("Database initialized successfully.")
    except sqlite3.Error as e:
        logging.error(f"Database initialization error: {e}")


def get_access_token():
    """Get Azure Access Token"""
    try:
        client = msal.ConfidentialClientApplication(
            CLIENT_ID,
            authority=AUTHORITY,
            client_credential=CLIENT_SECRET
        )

        token_result = client.acquire_token_silent(SCOPE, account=None)
        if not token_result:
            token_result = client.acquire_token_for_client(scopes=SCOPE)

        if "access_token" in token_result:
            logging.info("Azure AD token acquired.")
            return f"Bearer {token_result['access_token']}"

        logging.error("Failed to acquire Azure token.")
        return None
    except Exception as e:
        logging.error(f"Error getting access token: {e}")
        return None


def oncall_message_check():
    """Check for unread voicemail messages"""
    access_token = get_access_token()
    if not access_token:
        logging.error("Unable to proceed: No access token available")
        return

    url = "https://graph.microsoft.com/v1.0/users/oncall@gpha.com/messages?$filter=IsRead eq false"
    headers = {"Authorization": access_token}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        messages = response.json().get("value", [])
        for message in messages:
            if "Voicemail message" in message.get("subject", ""):
                person_on_call = get_on_call_person(access_token)

                if person_on_call:
                    contact = get_contact_info(person_on_call)
                    if not contact:
                        logging.error(f"No contact information found for {person_on_call}")
                        continue

                    number, email = contact
                    logging.info(f"On-call person: {person_on_call}, Number: {number}, Email: {email}")

                    if number:
                        make_outbound_call(number)

                    message_id = message.get('id')
                    if message_id:
                        check_if_initial(message_id, email)
                else:
                    logging.warning("No on-call person identified")

    except requests.RequestException as e:
        logging.error(f"Error fetching messages: {e}")


def get_on_call_person(access_token):
    """Get On-Call Person based on calendar events"""
    now = datetime.now()
    hour = now.hour
    weekday = now.weekday()  # Monday = 0, Sunday = 6
    today = date.today()

    # Calculate start and end times for calendar query
    starttime = (today - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
    endtime = today.strftime("%Y-%m-%dT23:59:59Z")

    url = "https://graph.microsoft.com/v1.0/groups/521439f6-39ac-4112-86d9-a467b29734e2/calendar/calendarView"
    params = {"startDateTime": starttime, "endDateTime": endtime}  # Fixed the curly braces issue
    headers = {"Authorization": access_token}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()

        events = response.json().get("value", [])

        # Determine which date to search for based on current time
        if hour >= 7 and weekday not in [5, 6]:
            search_date = today
        elif hour < 7 and weekday not in [0, 5, 6]:
            search_date = today - timedelta(days=1)
        elif weekday == 5:
            search_date = today - timedelta(days=1)
        elif weekday == 6:
            search_date = today - timedelta(days=2)
        elif hour < 7 and weekday == 0:
            search_date = today - timedelta(days=3)
        else:
            search_date = today  # Default case

        # Convert to the required format
        search_date = search_date.strftime('%Y-%m-%dT00:00:00.0000000')

        for event in events:
            if ("On Call" in event.get("subject", "") and
                    event.get('start', {}).get('dateTime') == search_date):
                on_call_name = event["subject"].split()[0]
                logging.info(f"Found on-call person: {on_call_name}")
                return on_call_name

        logging.warning(f"No on-call event found for date: {search_date}")
        return None

    except requests.RequestException as e:
        logging.error(f"Error fetching on-call person: {e}")
        return None


def get_contact_info(person):
    """Get contact information for a person"""
    if not person:
        return None

    contact = CONTACTS.get(person)
    if not contact:
        logging.warning(f"No contact information found for {person}")

    return contact


def make_outbound_call(number):
    """Make an outbound call using Twilio"""
    if not number:
        logging.error("No phone number provided for outbound call")
        return False

    try:
        call = twilio_client.calls.create(
            to=number,
            from_=TWILIO_FROM,
            twiml='<Response><Say voice="Polly.Joanna" rate="slow">Hello, there is an un red on-call message available for review on the email system.</Say></Response>'
        )
        logging.info(f"Call initiated to {number} (SID: {call.sid})")
        return True
    except Exception as e:
        logging.error(f"Error making call: {e}")
        return False


def check_if_initial(message_id, email):
    """Check if it is initial alert for message and create ticket if needed"""
    if not message_id:
        logging.error("No message ID provided")
        return

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM messages WHERE messageID = ?", (message_id,))
            rows = cursor.fetchall()

            if not rows:
                logging.info(f"Creating new ticket for message {message_id}")
                ticket_id = create_solarwinds_ticket(email)
                if ticket_id:
                    cursor.execute("INSERT INTO messages VALUES(?,?)", (message_id, ticket_id))
                    logging.info(f"Created ticket {ticket_id} for message {message_id}")
            else:
                logging.info(f"Message {message_id} already processed")
    except sqlite3.Error as e:
        logging.error(f"Database error in check_if_initial: {e}")


def create_solarwinds_ticket(email):
    """Create a ticket in Solarwinds"""
    if not email:
        logging.error("No email provided for ticket creation")
        return None

    auth_token = f"Bearer {SOLARWINDS_TOKEN}"
    url = "https://api.samanage.com/incidents.json"

    headers = {
        "X-Samanage-Authorization": auth_token,
        "Accept": "application/vnd.samanage.v2.1+json",
        "Content-Type": "application/json",
    }

    payload = {
        "incident": {
            "name": "On-call Alert",
            "description": "Automated alert from on-call monitoring system",
            "assignee": {
                "email": email
            },
            "priority": "Critical",
            "category": {
                "name": "Administrative"
            },
            "subcategory": {
                "name": "On Call"
            }
        }
    }

    try:
        response = requests.post(url, data=json.dumps(payload), headers=headers, timeout=10)
        response.raise_for_status()

        incident_data = response.json()
        incident_number = incident_data.get("number")

        if incident_number:
            logging.info(f"Created Solarwinds ticket: {incident_number}")
            return incident_number
        else:
            logging.error("No incident number returned from Solarwinds")
            return None

    except requests.RequestException as e:
        logging.error(f"Error creating Solarwinds Ticket: {e}")
        logging.error(f"Response code: {response.status_code}")
        logging.error(f"Response text: {response.text}")
        return None


if __name__ == "__main__":
    logging.info("On-call monitoring service started.")

    # Initialize the database
    initialize_database()

    # Run an initial check
    oncall_message_check()

    # Schedule regular checks every 5 minutes, 24/7/365
    schedule.every(5).minutes.do(oncall_message_check)

    # Main loop
    while True:
        schedule.run_pending()
        time.sleep(30)
