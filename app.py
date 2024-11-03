"""
This module provides a Flask web application for displaying real-time bus arrival information
for Hamilton, Ontario using GTFS (General Transit Feed Specification) data.

The application fetches and parses both static and real-time GTFS data to provide users with
up-to-date bus arrival times at specified stops.

Routes:
- `/`: Renders the main index page where users can input a stop ID.
- `/next-bus`: Fetches and displays the next buses arriving at the specified stop.
- `/autocomplete`: Provides autocomplete suggestions for stop names based on user input.

Functions:
- download_and_extract_gtfs_static: Downloads and extracts GTFS static data from a ZIP file.
- load_stops: Loads stop data from the GTFS static data.
- load_routes: Loads route data from the GTFS static data.
- load_trips: Loads trip data from the GTFS static data.
- index: Renders the main index page.
- get_next_bus: Fetches and displays the next buses arriving at the specified stop.
- autocomplete: Provides autocomplete suggestions for stop names.

Constants:
- GTFS_REALTIME_URL: URL for the GTFS real-time feed.
- GTFS_STATIC_URL: URL for the GTFS static feed.
- gtfs_timezone: Timezone for Hamilton, Ontario.

Global Variables:
- gtfs_zip: In-memory file containing the extracted GTFS static data.
- stops: List of stops loaded from the GTFS static data.
- stops_dict: Dictionary mapping stop IDs to stop names.
- routes: Dictionary mapping route IDs to route short names.
- trips: Dictionary mapping trip IDs to trip headsigns.
"""

import os
import time
from datetime import datetime, timedelta
import zipfile
import io
import csv
import logging
import pytz
import requests
from requests.exceptions import RequestException, SSLError, ConnectionError, Timeout
from flask import Flask, request, render_template, jsonify, send_from_directory
from google.transit import gtfs_realtime_pb2


log_level = os.getenv('LOG_LEVEL', 'INFO').upper()

logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='[%(asctime)s +0000] [%(process)d] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

app = Flask(__name__)

# GTFS Realtime and Static Feed URLs
GTFS_REALTIME_URL = os.environ.get('GTFS_REALTIME_URL', 'https://opendata.hamilton.ca/GTFS-RT/GTFS_TripUpdates.pb')
GTFS_STATIC_URL = os.environ.get('GTFS_STATIC_URL', 'https://opendata.hamilton.ca/GTFS-Static/Fall2024_GTFSstatic.zip')

# Local timezone of transit agency
gtfs_timezone = pytz.timezone(os.getenv('TZ', 'America/Toronto'))

# Agency name environment variable
agency_name = os.environ.get('AGENCY_NAME')

# Agency Logo URL environment variable
logo_url = os.environ.get('AGENCY_LOGO_URL')

# JSON Mode Flag
json_mode = os.environ.get('JSON_MODE', 'false').lower() == 'true'

def parse_static_time(time_str, base_date):
    # Handle times past midnight (e.g., "25:30:00")
    hours, minutes, seconds = map(int, time_str.split(':'))
    extra_days = hours // 24
    hours = hours % 24

    time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    dt = datetime.strptime(time_str, '%H:%M:%S')

    return base_date.replace(
        hour=dt.hour,
        minute=dt.minute,
        second=dt.second
    ) + timedelta(days=extra_days)

class GTFSDataManager:
    def __init__(self):
        self.stops = []
        self.stops_dict = {}
        self.routes = {}
        self.stop_times = []
        self.trips = {}
        self.last_update = None
        self.cache_file = 'gtfs_static_cache.zip'
        self.cache_duration = timedelta(days=1)
        self.load_static_data()

    def is_cache_valid(self):
        if os.path.exists(self.cache_file):
            cache_age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(self.cache_file))
            return cache_age <= self.cache_duration
        return False

    def download_gtfs_static(self):
        logging.info(f"Downloading GTFS static data: {GTFS_STATIC_URL}")
        response = requests.get(GTFS_STATIC_URL)
        if response.status_code != 200:
            raise Exception(f'Failed to download GTFS static data: {response.status_code}')
        with open(self.cache_file, 'wb') as f:
            f.write(response.content)
        return response.content

    def load_static_data(self):
        try:
            if self.is_cache_valid():
                with open(self.cache_file, 'rb') as f:
                    content = f.read()
            else:
                content = self.download_gtfs_static()

            with zipfile.ZipFile(io.BytesIO(content)) as gtfs_zip:
                # Load stops
                with gtfs_zip.open('stops.txt') as stops_file:
                    reader = csv.DictReader(io.TextIOWrapper(stops_file, encoding='utf-8'))
                    self.stops = [{'stop_id': row['stop_id'], 'stop_name': row['stop_name']} for row in reader]
                self.stops_dict = {stop['stop_id']: stop['stop_name'] for stop in self.stops}

                # Load routes
                with gtfs_zip.open('routes.txt') as routes_file:
                    reader = csv.DictReader(io.TextIOWrapper(routes_file, encoding='utf-8'))
                    self.routes = {row['route_id']: row['route_short_name'] for row in reader}

                # Load trips
                with gtfs_zip.open('trips.txt') as trips_file:
                    reader = csv.DictReader(io.TextIOWrapper(trips_file, encoding='utf-8'))
                    self.trips = {row['trip_id']: {'route_id': row['route_id'], 'headsign': row['trip_headsign']} 
                                for row in reader}

                # Load stop times
                with gtfs_zip.open('stop_times.txt') as stop_times_file:
                    reader = csv.DictReader(io.TextIOWrapper(stop_times_file, encoding='utf-8'))
                    self.stop_times = [{
                        'trip_id': row['trip_id'],
                        'arrival_time': row['arrival_time'],
                        'stop_id': row['stop_id']
                    } for row in reader]

            self.last_update = datetime.now()
            logging.info("Successfully loaded GTFS static data")

        except Exception as e:
            logging.error(f"Error loading GTFS static data: {str(e)}")
            raise

    def get_static_times(self, stop_id, current_time):
        static_buses = []
        base_date = current_time.replace(hour=0, minute=0, second=0, microsecond=0)

        for stop_time in self.stop_times:
            if stop_time['stop_id'] == stop_id:
                trip_id = stop_time['trip_id']
                trip_info = self.trips.get(trip_id)

                if trip_info:
                    route_id = trip_info['route_id']
                    arrival_time = parse_static_time(stop_time['arrival_time'], base_date)

                    if arrival_time >= current_time:
                        static_buses.append({
                            'arrival_time': arrival_time,
                            'route_id': route_id,
                            'route_name': self.routes.get(route_id, route_id),
                            'trip_headsign': trip_info['headsign'],
                            'is_realtime': False
                        })

        return static_buses

# Initialize the GTFS data manager
gtfs_manager = GTFSDataManager()

def format_countdown(minutes, is_realtime):
    prefix = "Arriving" if is_realtime else "Scheduled"

    if minutes == 0:
        return f"{prefix} now"
    elif minutes == 1:
        return f"{prefix} in 1 minute"
    elif minutes >= 60:
        hours = minutes // 60
        remaining_minutes = minutes % 60
        if remaining_minutes == 0:
            return f"{prefix} in {hours} hr{'s' if hours > 1 else ''}"
        return f"{prefix} in {hours} hr{'s' if hours > 1 else ''} {remaining_minutes} min"
    else:
        return f"{prefix} in {minutes} minutes"

@app.route('/')
def index():
    stop_id = request.args.get('stop_id')
    return render_template('index.html',
                           stop_id=stop_id,
                           agency_name=agency_name,
                           logo_url=logo_url)

@app.route('/next-bus', methods=['GET'])
def get_next_bus():
    stop_id = request.args.get('stop_id')
    if not stop_id:
        error_message = 'No stop selected'
        if json_mode:
            return jsonify({'error': error_message}), 400
        else:
            return render_template('bus_times.html', error=error_message)

    if stop_id not in gtfs_manager.stops_dict:
        error_message = 'Invalid stop ID'
        if json_mode:
            return jsonify({'error': error_message}), 400
        else:
            return render_template('bus_times.html', error=error_message)

    current_time = datetime.now(gtfs_timezone)
    next_buses = []
    realtime_available = True

    # Retry logic for fetching GTFS Realtime data
    max_retries = 3
    attempt = 0
    while attempt < max_retries:
        try:
            # Attempt to fetch GTFS Realtime data
            response = requests.get(GTFS_REALTIME_URL, timeout=5)
            response.raise_for_status()

            # Parse the GTFS Realtime data
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(response.content)

            # Process realtime data
            for entity in feed.entity:
                if entity.HasField('trip_update'):
                    trip_update = entity.trip_update
                    trip_id = trip_update.trip.trip_id
                    route_id = trip_update.trip.route_id
                    trip_headsign = gtfs_manager.trips.get(trip_id, {}).get('headsign', '')
                    route_name = gtfs_manager.routes.get(route_id, route_id)

                    for stop_time_update in trip_update.stop_time_update:
                        if stop_time_update.stop_id == stop_id:
                            arrival_time = datetime.fromtimestamp(
                                stop_time_update.arrival.time,
                                gtfs_timezone
                            )
                            if arrival_time >= current_time:
                                next_buses.append({
                                    'arrival_time': arrival_time,
                                    'route_id': route_id,
                                    'route_name': route_name,
                                    'trip_headsign': trip_headsign,
                                    'is_realtime': True
                                })
            break  # Exit retry loop if successful

        except Exception:
            attempt += 1
            time.sleep(5)  # Wait before retrying

        if attempt == max_retries:
            logging.error(f"Failed to fetch real-time data after 3 attempts: {GTFS_REALTIME_URL}")
            realtime_available = False

    # Sort live buses by arrival time
    next_buses.sort(key=lambda x: x['arrival_time'])
    last_live_arrival = next_buses[-1]['arrival_time'] if next_buses else current_time
    four_hours_from_now = current_time + timedelta(hours=4)

    # Gather scheduled buses
    if len(next_buses) < 5:
        static_buses = gtfs_manager.get_static_times(stop_id, current_time)

        # Only add scheduled buses that are after the last live bus and within 4 hours
        for bus in static_buses:
            if last_live_arrival <= bus['arrival_time'] <= four_hours_from_now:
                next_buses.append(bus)
                if len(next_buses) == 5:
                    break
            else:
                # Log excluded buses at debug level
                logging.debug(
                    f"Excluding scheduled bus with arrival time {bus['arrival_time']} "
                    f"for route {bus['route_name']} due to filter constraints "
                    f"(last_live_arrival: {last_live_arrival}, four_hours_from_now: {four_hours_from_now})"
                )

    # Sort the list of all buses (live and scheduled) by arrival time
    next_buses.sort(key=lambda x: x['arrival_time'])
    next_buses = next_buses[:5]
    stop_name = gtfs_manager.stops_dict[stop_id]
    now = datetime.now(gtfs_timezone)

    # Calculate countdown and format times
    for bus in next_buses:
        countdown = int((bus['arrival_time'] - now).total_seconds() / 60)
        bus['countdown'] = max(countdown, 0)
        bus['countdown_text'] = format_countdown(bus['countdown'], bus['is_realtime'])
        bus['arrival_time_formatted'] = bus['arrival_time'].strftime('%I:%M %p')
        bus['arrival_time'] = bus['arrival_time'].isoformat()

    if json_mode:
        return jsonify({
            'buses': next_buses,
            'stop_name': stop_name,
            'stop_id': stop_id
        })
    else:
        return render_template('bus_times.html',
                               buses=next_buses,
                               stop_name=stop_name,
                               stop_id=stop_id,
                               agency_name=agency_name,
                               logo_url=logo_url)

@app.route('/autocomplete', methods=['GET'])
def autocomplete():
    query = request.args.get('q', '').lower()
    suggestions = [
        {'stop_id': stop['stop_id'], 'stop_name': stop['stop_name']}
        for stop in gtfs_manager.stops
        if query in stop['stop_name'].lower()
    ][:10]
    return jsonify(suggestions)

# Routes for PWA
@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json')

@app.route('/service-worker.js')
def service_worker():
    return send_from_directory('static', 'service-worker.js')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
