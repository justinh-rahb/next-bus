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
from datetime import datetime
import zipfile
import io
import csv
import logging
import pytz
import requests
from requests.exceptions import RequestException, SSLError, ConnectionError, Timeout
from flask import Flask, request, render_template, jsonify, send_from_directory
from google.transit import gtfs_realtime_pb2

logging.basicConfig(level=logging.INFO)

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

# Download and parse GTFS static data
def download_and_extract_gtfs_static():
    response = requests.get(GTFS_STATIC_URL)
    if response.status_code != 200:
        raise Exception(f'Failed to download GTFS static data: {response.status_code}')

    # Create an in-memory file
    gtfs_zip = zipfile.ZipFile(io.BytesIO(response.content))
    return gtfs_zip

# Load stop data
def load_stops(gtfs_zip):
    stops = []
    with gtfs_zip.open('stops.txt') as stops_file:
        reader = csv.DictReader(io.TextIOWrapper(stops_file, encoding='utf-8'))
        for row in reader:
            stops.append({'stop_id': row['stop_id'], 'stop_name': row['stop_name']})
    return stops

# Load route data
def load_routes(gtfs_zip):
    routes = {}
    with gtfs_zip.open('routes.txt') as routes_file:
        reader = csv.DictReader(io.TextIOWrapper(routes_file, encoding='utf-8'))
        for row in reader:
            routes[row['route_id']] = row['route_short_name']
    return routes

# Load trip data
def load_trips(gtfs_zip):
    trips = {}
    with gtfs_zip.open('trips.txt') as trips_file:
        reader = csv.DictReader(io.TextIOWrapper(trips_file, encoding='utf-8'))
        for row in reader:
            trips[row['trip_id']] = row['trip_headsign']
    return trips

def load_static_schedule(gtfs_zip):
    # Load stop_times.txt
    stop_times = []
    with gtfs_zip.open('stop_times.txt') as stop_times_file:
        reader = csv.DictReader(io.TextIOWrapper(stop_times_file, encoding='utf-8'))
        for row in reader:
            stop_times.append({
                'trip_id': row['trip_id'],
                'arrival_time': row['arrival_time'],
                'stop_id': row['stop_id']
            })
    return stop_times

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

def format_countdown(minutes):
    if minutes == 0:
        return "Arriving right now"
    elif minutes == 1:
        return "Arriving in 1 minute"
    elif minutes >= 60:
        hours = minutes // 60
        remaining_minutes = minutes % 60
        return f"Arriving in {hours} hr {remaining_minutes} min"
    else:
        return f"Arriving in {minutes} minutes"

def get_static_times(stop_id, current_time, stop_times, routes, trips):
    static_buses = []
    base_date = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
    
    for stop_time in stop_times:
        if stop_time['stop_id'] == stop_id:
            trip_id = stop_time['trip_id']
            route_id = next((trip['route_id'] for trip in trips.values() if trip['trip_id'] == trip_id), None)
            
            if route_id:
                arrival_time = parse_static_time(stop_time['arrival_time'], base_date)
                
                if arrival_time >= current_time:
                    static_buses.append({
                        'arrival_time': arrival_time,
                        'route_id': route_id,
                        'route_name': routes.get(route_id, route_id),
                        'trip_headsign': trips.get(trip_id, {}).get('headsign', ''),
                        'is_realtime': False
                    })
    
    return static_buses

# Load all GTFS static data
gtfs_zip = download_and_extract_gtfs_static()
stops = load_stops(gtfs_zip)
stops_dict = {stop['stop_id']: stop['stop_name'] for stop in stops}
routes = load_routes(gtfs_zip)
stop_times = load_static_schedule(gtfs_zip)
trips = load_trips(gtfs_zip)

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

    if stop_id not in stops_dict:
        error_message = 'Invalid stop ID'
        if json_mode:
            return jsonify({'error': error_message}), 400
        else:
            return render_template('bus_times.html', error=error_message)

    next_buses = []
    realtime_available = True

    try:
        # Fetch GTFS Realtime data
        response = requests.get(GTFS_REALTIME_URL, timeout=15)
        response.raise_for_status()
        
        # Parse the GTFS Realtime data
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)
        
        current_time = datetime.now(gtfs_timezone)
        
        for entity in feed.entity:
            if entity.HasField('trip_update'):
                trip_update = entity.trip_update
                trip_id = trip_update.trip.trip_id
                route_id = trip_update.trip.route_id
                trip_headsign = trips.get(trip_id, '')
                route_name = routes.get(route_id, route_id)

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
                            
    except Exception as e:
        logging.error(f"Error fetching realtime data: {e}")
        realtime_available = False

    # If no realtime data or realtime fetch failed, get static times
    if not next_buses or not realtime_available:
        current_time = datetime.now(gtfs_timezone)
        static_buses = get_static_times(stop_id, current_time, stop_times, routes, trips)
        next_buses.extend(static_buses)

    if not next_buses:
        error_message = 'No upcoming buses found for this stop.'
        if json_mode:
            return jsonify({'error': error_message, 'stop_id': stop_id}), 200
        else:
            return render_template('bus_times.html', error=error_message, stop_id=stop_id)

    # Sort buses by arrival time
    next_buses.sort(key=lambda x: x['arrival_time'])
    next_buses = next_buses[:5]  # Limit to next 5 buses

    stop_name = stops_dict[stop_id]
    now = datetime.now(gtfs_timezone)

    # Calculate countdown and format times
    for bus in next_buses:
        countdown = int((bus['arrival_time'] - now).total_seconds() / 60)
        bus['countdown'] = max(countdown, 0)
        bus['countdown_text'] = format_countdown(bus['countdown'])
        bus['arrival_type'] = 'Arriving' if bus['is_realtime'] else 'Scheduled'
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
        for stop in stops
        if query in stop['stop_name'].lower()
    ][:10]  # Limit to 10 suggestions
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
