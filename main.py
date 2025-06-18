import requests
from bs4 import BeautifulSoup
import time
import logging
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
from flask import Flask, request
import threading
import os
from datetime import datetime
import re

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class EventbriteMonitor:
    def __init__(self, twilio_sid, twilio_token, twilio_phone, your_phone, webhook_url="https://demo.twilio.com/welcome/voice/"):
        """
        Initialize the Eventbrite monitor

        Args:
            twilio_sid: Your Twilio Account SID
            twilio_token: Your Twilio Auth Token
            twilio_phone: Your Twilio phone number (format: +1234567890)
            your_phone: Your phone number to call (format: +1234567890)
            webhook_url: Optional public webhook URL for TwiML responses
        """
        self.twilio_client = Client(twilio_sid, twilio_token)
        self.twilio_phone = twilio_phone
        self.your_phone = your_phone
        self.webhook_url = webhook_url

        # Flask app for TwiML webhook
        self.app = Flask(__name__)
        self.setup_webhook()

        # Monitoring state
        self.monitoring = False
        self.monitored_events = {}

        # Headers to mimic a real browser
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0'
        }

    def setup_webhook(self):
        """Setup Flask webhook for TwiML response"""
        @self.app.route('/voice', methods=['POST'])
        def voice_webhook():
            response = VoiceResponse()

            # Speak the message clearly
            response.say(
                "Attention! Eventbrite tickets are now available for your monitored event. "
                "Please check the event page immediately to purchase tickets. "
                "This is an automated alert from your ticket monitor.",
                voice='alice',
                rate='medium'
            )

            # Add a pause and repeat key info
            response.pause(length=1)
            response.say(
                "Tickets are available now. Check Eventbrite immediately.",
                voice='alice',
                rate='slow'
            )

            return str(response)

    def check_event_availability(self, event_url):
        """
        Enhanced check for Eventbrite ticket availability with better detection

        Args:
            event_url: The Eventbrite event URL

        Returns:
            dict: Status information about the event
        """
        try:
            # Add session for better handling
            session = requests.Session()
            session.headers.update(self.headers)

            response = session.get(event_url, timeout=15)
            response.raise_for_status()

            # Wait 10 seconds for page to fully load
            logger.info("Waiting 10 seconds for page to load...")
            time.sleep(10)

            soup = BeautifulSoup(response.content, 'html.parser')
            page_text = soup.get_text().lower()

            # Get event title
            title_elem = (soup.find('h1', class_=re.compile('event-title|title')) or 
                         soup.find('h1') or 
                         soup.find('title'))
            event_title = title_elem.get_text().strip() if title_elem else "Unknown Event"

            # Enhanced sold out detection
            sold_out_patterns = [
                r'\bsold out\b',
                r'\bregistration closed\b',
                r'\btickets unavailable\b',
                r'\bevent has ended\b',
                r'\bregistration is closed\b',
                r'\bno longer available\b',
                r'\bunavailable\b',
                r'\bsales ended\b',
                r'\bsale has ended\b',
                r'\bevents ended\b'
            ]

            # Check for sold out text patterns
            is_sold_out_text = any(re.search(pattern, page_text) for pattern in sold_out_patterns)

            # Check for availability indicators
            availability_patterns = [
                r'select\s+date\s+and\s+time',
                r'reserve\s+a\s+spot',
                r'get\s+tickets',
                r'buy\s+now',
                r'purchase\s+tickets',
                r'register\s+now'
            ]

            has_availability_text = any(re.search(pattern, page_text, re.IGNORECASE) for pattern in availability_patterns)

            # Look for ticket/register buttons with more specific selectors
            ticket_selectors = [
                'button[data-spec="eds-button"]',
                'a[data-spec="eds-button"]',
                'button[class*="ticket"]',
                'a[class*="ticket"]',
                'button[class*="register"]',
                'a[class*="register"]',
                '.ticket-button',
                '.register-button',
                '[data-automation="checkout-widget-register-button"]'
            ]

            available_buttons = []
            total_buttons = 0

            for selector in ticket_selectors:
                buttons = soup.select(selector)
                for button in buttons:
                    total_buttons += 1
                    button_text = button.get_text().lower()
                    button_classes = ' '.join(button.get('class', [])).lower()

                    # Check if button is available
                    is_disabled = (button.get('disabled') or 
                                 'disabled' in button_classes or
                                 'sold-out' in button_classes or
                                 'unavailable' in button_classes)

                    # Check button text for availability indicators
                    has_ticket_text = any(word in button_text for word in 
                                        ['ticket', 'register', 'get tickets', 'buy now', 'purchase', 'reserve a spot'])

                    if has_ticket_text and not is_disabled:
                        available_buttons.append(button)

            # Check for specific Eventbrite sold out indicators in HTML
            sold_out_elements = soup.find_all(class_=re.compile(r'sold.?out|unavailable', re.I))
            has_sold_out_elements = len(sold_out_elements) > 0

            # Check for "0 remaining" or similar quantity indicators
            quantity_patterns = [
                r'\b0\s+remaining\b',
                r'\b0\s+tickets?\s+remaining\b',
                r'\b0\s+spots?\s+remaining\b'
            ]
            has_zero_remaining = any(re.search(pattern, page_text) for pattern in quantity_patterns)

            # Final availability determination - Updated logic
            is_definitely_sold_out = (is_sold_out_text or 
                                    has_sold_out_elements or 
                                    has_zero_remaining) and not has_availability_text

            # If we find availability text OR available buttons, consider it available
            is_available = (has_availability_text or len(available_buttons) > 0) and not is_definitely_sold_out

            status = {
                'url': event_url,
                'title': event_title,
                'available': is_available,
                'sold_out': is_definitely_sold_out,
                'total_buttons_found': total_buttons,
                'available_buttons': len(available_buttons),
                'sold_out_text_found': is_sold_out_text,
                'sold_out_elements_found': has_sold_out_elements,
                'zero_remaining_found': has_zero_remaining,
                'availability_text_found': has_availability_text,
                'checked_at': datetime.now().isoformat()
            }

            logger.info(f"Checked '{event_title}': {'Available' if is_available else 'Sold Out'} "
                       f"(Buttons: {len(available_buttons)}/{total_buttons}, Availability text: {has_availability_text})")

            return status

        except requests.RequestException as e:
            logger.error(f"Error checking event {event_url}: {e}")
            return {
                'url': event_url,
                'title': 'Error - Network Issue',
                'available': False,
                'error': str(e),
                'checked_at': datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Unexpected error checking event {event_url}: {e}")
            return {
                'url': event_url,
                'title': 'Error - Parse Issue',
                'available': False,
                'error': str(e),
                'checked_at': datetime.now().isoformat()
            }

    def make_alert_call(self, event_info, webhook_url=None):
        """
        Make a phone call to alert about ticket availability

        Args:
            event_info: Dictionary containing event information
            webhook_url: Public webhook URL for TwiML response
        """
        try:
            # Always use direct TwiML since you don't want webhooks
            twiml = VoiceResponse()
            twiml.say(
                f"Alert! Eventbrite tickets are now available for {event_info['title']}. "
                "Check the event page immediately to purchase tickets. "
                "I repeat, tickets are now available.",
                voice='alice',
                rate='medium'
            )

            call = self.twilio_client.calls.create(
                to=self.your_phone,
                from_=self.twilio_phone,
                twiml=str(twiml)
            )

            logger.info(f"Alert call initiated for {event_info['title']} - Call SID: {call.sid}")
            return call.sid

        except Exception as e:
            logger.error(f"Error making alert call: {e}")
            return None

    def add_event(self, event_url, check_interval=1800):  # Default 30 minutes
        """
        Add an event to monitor

        Args:
            event_url: Eventbrite event URL
            check_interval: How often to check in seconds (default: 30 minutes = 1800 seconds)
        """
        self.monitored_events[event_url] = {
            'interval': check_interval,
            'last_check': 0,  # Timestamp of last check
            'last_status': None,
            'alert_sent': False
        }
        logger.info(f"Added event to monitor: {event_url} (Check every {check_interval//60} minutes)")

    def remove_event(self, event_url):
        """Remove an event from monitoring"""
        if event_url in self.monitored_events:
            del self.monitored_events[event_url]
            logger.info(f"Removed event from monitoring: {event_url}")

    def monitor_events(self):
        """Main monitoring loop with individual intervals for each event"""
        self.monitoring = True
        logger.info("Starting event monitoring...")

        while self.monitoring:
            current_time = time.time()

            for event_url, event_config in self.monitored_events.items():
                try:
                    # Check if it's time to check this event
                    time_since_last_check = current_time - event_config['last_check']

                    if time_since_last_check >= event_config['interval']:
                        # Check event status
                        status = self.check_event_availability(event_url)

                        # Check if tickets became available (transition from sold out to available)
                        # Alert if available and no alert sent yet
                        if (status['available'] and not event_config['alert_sent']):
                            logger.info(f"🎟️ TICKETS AVAILABLE: {status['title']}")
                            # Make alert call
                            call_sid = self.make_alert_call(status)
                            if call_sid:
                                # Store the call SID to check later
                                event_config['last_call_sid'] = call_sid
                                event_config['call_made_at'] = current_time

                        # Check if previous calls were answered (after 2 minutes) - SEPARATE from elif
                        if (event_config.get('last_call_sid') and 
                            current_time - event_config.get('call_made_at', 0) > 120):  # 2 minutes

                            if self.check_call_answered(event_config['last_call_sid']):
                                # Call was answered - stop monitoring this event
                                logger.info(f"✅ Call answered for {status['title']} - stopping monitoring")
                                event_config['alert_sent'] = True  # This stops future calls
                            else:
                                # Call not answered - reset to make another call next cycle
                                logger.info(f"📞 Call not answered for {status['title']} - will try again")
                                event_config['last_call_sid'] = None
                                event_config['call_made_at'] = None

                        # Update event config
                        event_config['last_check'] = current_time
                        event_config['last_status'] = status

                except Exception as e:
                    logger.error(f"Error monitoring event {event_url}: {e}")

            # Sleep for 60 seconds before next loop iteration
            time.sleep(60)

    def check_call_answered(self, call_sid):
        """
        Check if a call was answered
        
        Args:
            call_sid: The Twilio call SID
            
        Returns:
            bool: True if call was answered, False otherwise
        """
        try:
            call = self.twilio_client.calls(call_sid).fetch()
            # Call status can be: queued, ringing, in-progress, completed, failed, busy, no-answer, canceled
            return call.status in ['completed', 'in-progress']
        except Exception as e:
            logger.error(f"Error checking call status: {e}")
            return False

    def start_monitoring(self, webhook_port=5000):
        """Start the monitoring process"""
        # Skip webhook server since we're using direct TwiML
        logger.info("Starting monitoring with direct TwiML calls (no webhook server needed)")

        # Start monitoring
        self.monitor_events()

    def stop_monitoring(self):
        """Stop the monitoring process"""
        self.monitoring = False
        logger.info("Monitoring stopped")

    def get_status(self):
        """Get current status of all monitored events"""
        status = {}
        current_time = time.time()

        for url, config in self.monitored_events.items():
            next_check_in = max(0, config['interval'] - (current_time - config['last_check']))
            status[url] = {
                'last_status': config['last_status'],
                'alert_sent': config['alert_sent'],
                'interval_minutes': config['interval'] // 60,
                'next_check_in_minutes': round(next_check_in / 60, 1),
                'last_check': datetime.fromtimestamp(config['last_check']).isoformat() if config['last_check'] > 0 else 'Never'
            }
        return status

    def print_status(self):
        """Print current monitoring status"""
        status = self.get_status()
        print("\n" + "="*80)
        print("EVENTBRITE MONITOR STATUS")
        print("="*80)

        for url, info in status.items():
            print(f"\nEvent: {info['last_status']['title'] if info['last_status'] else 'Unknown'}")
            print(f"URL: {url}")
            print(f"Status: {'Available' if info['last_status'] and info['last_status']['available'] else 'Sold Out'}")
            print(f"Check Interval: {info['interval_minutes']} minutes")
            print(f"Next Check: {info['next_check_in_minutes']} minutes")
            print(f"Last Check: {info['last_check']}")
            print(f"Alert Sent: {'Yes' if info['alert_sent'] else 'No'}")

        print("\n" + "="*80 + "\n")


def main():
    """Example usage"""
    # Replace with your actual Twilio credentials and phone numbers
    TWILIO_SID = os.getenv('TWILIO_SID')
    TWILIO_TOKEN = os.getenv('TWILIO_TOKEN')
    TWILIO_PHONE = os.getenv('TWILIO_PHONE_NUMBER')  # Your Twilio phone number
    YOUR_PHONE = os.getenv('YOUR_PHONE_NUMBER')    # Your phone number

    # Initialize monitor (no webhook URL needed - will use direct TwiML)
    monitor = EventbriteMonitor(TWILIO_SID, TWILIO_TOKEN, TWILIO_PHONE, YOUR_PHONE)

    # Add events to monitor
    event_urls = [
        "https://www.eventbrite.com/e/north-brunswick-nj-main-street-north-brunswick-community-day-tickets-1406481517079?aff=ebdsoporgprofile",
        "https://www.eventbrite.com/e/cava-north-bevery-plaza-community-day-beverly-ma-tickets-1416951894259?aff=erelexpmlt",
        # Add more event URLs as needed
    ]

    for url in event_urls:
        monitor.add_event(url, check_interval=1800)  # Check every 30 minutes

    # Print initial status
    monitor.print_status()

    try:
        # Start monitoring (this will run indefinitely)
        monitor.start_monitoring(webhook_port=5000)
    except KeyboardInterrupt:
        logger.info("Stopping monitor...")
        monitor.stop_monitoring()


if __name__ == "__main__":
    main()