# Next Bus

Welcome to the **Next Bus Time** application! This web app provides real-time bus arrival information for the Hamilton Street Railway (HSR) using Flask and HTMX. Users can search for bus stops by name, view upcoming buses with countdown timers, and enjoy an interface similar to a bus stop display.

## Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Running the Application Locally](#running-the-application-locally)
- [Configuration](#configuration)
- [Project Structure](#project-structure)
- [Deployment](#deployment)
- [Contributing](#contributing)
- [License](#license)

## Features

- **Real-Time Bus Information**: Fetches live bus data from HSR's GTFS-Realtime feed.
- **Stop Search with Autocomplete**: Search for stops by name with instant suggestions.
- **Countdown Timers**: Displays time remaining until the next bus arrives.
- **Auto-Refresh**: Bus times refresh automatically at selectable intervals.
- **Responsive UI**: A user interface that mimics a bus stop display, optimized for various devices.
- **Fade-Out Transition**: Smooth transitions between the search screen and results.
- **No JavaScript Frameworks Required**: Uses HTMX for dynamic interactions without heavy JavaScript frameworks.

## Prerequisites

- **Python 3.7 or higher**
- **pip** (Python package installer)
- **Git** (optional, for cloning the repository)

## Installation

1. **Clone the Repository**

   ```bash
   git clone https://github.com/justinh-rahb/next-bus.git
   cd next-bus
   ```

2. **Create a Virtual Environment**

   It's recommended to use a virtual environment to manage dependencies.

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows, use venv\Scripts\activate
   ```

3. **Install Dependencies**

   ```bash
   pip install -r requirements.txt
   ```

## Running the Application Locally

1. **Set Environment Variables (Optional)**

   You can configure the GTFS feed URLs via environment variables. If not set, default URLs will be used.

   ```bash
   export GTFS_REALTIME_URL='https://opendata.hamilton.ca/GTFS-RT/GTFS_TripUpdates.pb'
   export GTFS_STATIC_URL='https://opendata.hamilton.ca/GTFS-Static/Fall2024_GTFSstatic.zip'
   ```

2. **Run the Application**

   ```bash
   python app.py
   ```

3. **Access the Application**

   Open your web browser and navigate to `http://localhost:5000/`.


## Configuration

- **Environment Variables**

  - `GTFS_REALTIME_URL`: URL for the GTFS-Realtime feed.
  - `GTFS_STATIC_URL`: URL for the GTFS static feed.
  - `PORT`: The port on which the app runs (default is `5000`).

- **Dependencies**

  All dependencies are listed in `requirements.txt`. Key dependencies include:

  - **Flask**: Web framework for Python.
  - **HTMX**: For dynamic frontend interactions.
  - **requests**: To make HTTP requests.
  - **gtfs-realtime-bindings**: To parse GTFS-Realtime protocol buffers.
  - **pytz**: For timezone handling.

## Project Structure

```
next_bus/
├── app.py
├── Procfile
├── requirements.txt
├── templates/
│   ├── base.html
│   ├── index.html
│   └── bus_times.html
├── static/
│   └── styles.css
```

- **app.py**: Main application file containing backend logic.
- **templates/**: HTML templates for rendering pages.
- **static/**: Static files like CSS and images.
- **requirements.txt**: Python dependencies.
- **Procfile**: Process file for deployment (e.g., with Gunicorn).

## Deployment

To deploy the application to a production environment, follow these general steps:

1. **Ensure All Dependencies Are Listed**

   Make sure `requirements.txt` includes all necessary packages.

2. **Configure Environment Variables**

   Set the environment variables `GTFS_REALTIME_URL`, `GTFS_STATIC_URL`, and `PORT` as needed for your environment.

3. **Use a Production WSGI Server**

   Use a production-ready server like **Gunicorn** to run the application.

   ```bash
   gunicorn app:app
   ```

4. **Set Up Process Management**

   Use a process manager like **Supervisor** or **systemd** to keep your application running.

5. **Configure SSL and Security**

   - Use HTTPS to secure data in transit.
   - Set up proper error handling and logging.
   - Disable debug mode in production.

6. **Choose a Deployment Platform**

   Deploy the application on a platform of your choice, such as:

   - **Virtual Private Server (VPS)**: Set up a VPS on providers like DigitalOcean, AWS EC2, or Linode.
   - **Platform as a Service (PaaS)**: Use services like Render, Railway, or Google App Engine.
   - **Containerization**: Dockerize your application and deploy using Kubernetes or services like AWS ECS.

## Contributing

Contributions are welcome! Please follow these steps:

1. **Fork the Repository**

   Click the "Fork" button at the top right of the repository page.

2. **Clone Your Fork**

   ```bash
   git clone https://github.com/justinh-rahb/next-bus.git
   ```

3. **Create a Feature Branch**

   ```bash
   git checkout -b feature/your-feature-name
   ```

4. **Commit Your Changes**

   ```bash
   git commit -am "Add new feature"
   ```

5. **Push to Your Fork**

   ```bash
   git push origin feature/your-feature-name
   ```

6. **Submit a Pull Request**

   Go to the original repository and create a pull request from your fork.

## License

This project is licensed under the [MIT License](LICENSE).

## Acknowledgements

- **Hamilton Street Railway (HSR)**: For providing open GTFS data feeds.
- **HTMX**: For enabling dynamic frontend interactions without heavy JavaScript frameworks.
- **Flask**: For providing a simple yet powerful web framework.

## Troubleshooting

- **No Upcoming Buses Found**

  Ensure that the stop you selected has scheduled buses at the current time.

- **Application Errors**

  Check the console output for any error messages. Common issues may include network connectivity or incorrect environment variable configurations.

- **Static Files Not Loading**

  Ensure that static files are correctly served in your deployment environment. Check the configuration of Whitenoise or your web server.


## Frequently Asked Questions

### How do I find valid stop names?

Begin typing the name of a bus stop in the search field, and the autocomplete feature will provide suggestions based on the HSR's GTFS data.

### Can I change the auto-refresh intervals?

Yes, you can select different refresh intervals (5s, 10s, 30s, 60s) from the dropdown menu on the bus times page.

### How does the countdown timer work?

The countdown timer calculates the time remaining in minutes until the next bus arrives at the selected stop, based on real-time data.

## Additional Resources

- **Flask Documentation**: [https://flask.palletsprojects.com/](https://flask.palletsprojects.com/)
- **HTMX Documentation**: [https://htmx.org/docs/](https://htmx.org/docs/)
- **HSR Open Data Portal**: [https://opendata.hamilton.ca/](https://opendata.hamilton.ca/)

**Thank you for using the Next Bus Time application!**