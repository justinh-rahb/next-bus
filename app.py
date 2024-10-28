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
- hsr_timezone: Timezone for Hamilton, Ontario.

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
from flask import Flask, request, render_template, jsonify
from google.transit import gtfs_realtime_pb2

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

# GTFS Realtime and Static Feed URLs
GTFS_REALTIME_URL = os.environ.get('GTFS_REALTIME_URL', 'https://opendata.hamilton.ca/GTFS-RT/GTFS_TripUpdates.pb')
GTFS_STATIC_URL = os.environ.get('GTFS_STATIC_URL', 'https://opendata.hamilton.ca/GTFS-Static/Fall2024_GTFSstatic.zip')

# Timezone for Hamilton, Ontario
hsr_timezone = pytz.timezone('America/Toronto')

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

# Load all GTFS static data
gtfs_zip = download_and_extract_gtfs_static()
stops = load_stops(gtfs_zip)
stops_dict = {stop['stop_id']: stop['stop_name'] for stop in stops}
routes = load_routes(gtfs_zip)
trips = load_trips(gtfs_zip)

@app.route('/')
def index():
    stop_id = request.args.get('stop_id')
    if stop_id:
        return render_template('index.html', stop_id=stop_id)
    else:
        return render_template('index.html')

@app.route('/next-bus', methods=['GET'])
def get_next_bus():
    stop_id = request.args.get('stop_id')
    if not stop_id:
        return render_template('bus_times.html', error='No stop selected')

    if stop_id not in stops_dict:
        return render_template('bus_times.html', error='Invalid stop ID')

    try:
        # Fetch GTFS Realtime data
        response = requests.get(GTFS_REALTIME_URL)
        if response.status_code != 200:
            raise Exception(f'Failed to fetch realtime data: {response.status_code}')

        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(response.content)

        current_time = int(time.time())

        next_buses = []
        for entity in feed.entity:
            if entity.HasField('trip_update'):
                trip_update = entity.trip_update
                trip_id = trip_update.trip.trip_id
                route_id = trip_update.trip.route_id
                trip_headsign = trips.get(trip_id, '')
                route_name = routes.get(route_id, route_id)

                for stop_time_update in trip_update.stop_time_update:
                    if stop_time_update.stop_id == stop_id:
                        arrival_time = stop_time_update.arrival.time
                        if arrival_time >= current_time:
                            # Convert UNIX timestamp to local time
                            local_arrival_time = datetime.fromtimestamp(arrival_time, hsr_timezone)
                            next_buses.append({
                                'arrival_time': local_arrival_time,
                                'route_id': route_id,
                                'route_name': route_name,
                                'trip_headsign': trip_headsign
                            })

        # Sort buses by arrival time
        next_buses.sort(key=lambda x: x['arrival_time'])

        # Limit to next 5 buses
        next_buses = next_buses[:5]

        stop_name = stops_dict[stop_id]

        # Calculate countdown and format arrival time
        now = datetime.now(hsr_timezone)
        for bus in next_buses:
            countdown = int((bus['arrival_time'] - now).total_seconds() / 60)
            bus['countdown'] = max(countdown, 0)
            bus['arrival_time_formatted'] = bus['arrival_time'].strftime('%I:%M %p')

        return render_template('bus_times.html', buses=next_buses, stop_name=stop_name, stop_id=stop_id)
    except Exception as e:
        print(f"An error occurred: {e}")
        return render_template('bus_times.html', error='An unexpected error occurred.')

@app.route('/autocomplete', methods=['GET'])
def autocomplete():
    query = request.args.get('q', '').lower()
    suggestions = [
        {'stop_id': stop['stop_id'], 'stop_name': stop['stop_name']}
        for stop in stops
        if query in stop['stop_name'].lower()
    ][:10]  # Limit to 10 suggestions
    return jsonify(suggestions)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
