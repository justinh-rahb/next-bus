document.addEventListener('DOMContentLoaded', function() {
    // Theme handling
    const themeToggle = document.getElementById('theme-toggle');
    const urlParams = new URLSearchParams(window.location.search);
    const urlTheme = urlParams.get('theme');
    const currentTheme = urlTheme || localStorage.getItem('theme') || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");

    // Only store in localStorage if it wasn't set via URL
    if (!urlTheme) {
        localStorage.setItem('theme', currentTheme);
    }

    document.documentElement.setAttribute('data-theme', currentTheme);

    function updateThemeIcon(theme) {
        const lightIcon = document.getElementById('light-icon');
        const darkIcon = document.getElementById('dark-icon');
        if (theme === 'dark') {
            lightIcon.style.display = 'none';
            darkIcon.style.display = 'inline-block';
        } else {
            lightIcon.style.display = 'inline-block';
            darkIcon.style.display = 'none';
        }
    }

    updateThemeIcon(currentTheme);

    if (themeToggle) {
        themeToggle.addEventListener('click', function() {
            let theme = document.documentElement.getAttribute('data-theme');
            theme = theme === "light" ? "dark" : "light";
            document.documentElement.setAttribute('data-theme', theme);
            localStorage.setItem('theme', theme);
            updateThemeIcon(theme);
        });
    }

    // Stop handling
    var stopNameInput = document.getElementById('stop_name');
    var stopIdInput = document.getElementById('stop_id');

    if (stopNameInput && stopIdInput) {
        var awesomplete = new Awesomplete(stopNameInput, {
            minChars: 1,
            maxItems: 10,
            autoFirst: true
        });

        stopNameInput.addEventListener('input', function() {
            var query = stopNameInput.value;
            if (query.length >= 1) {
                fetch('/autocomplete?q=' + encodeURIComponent(query))
                    .then(response => response.json())
                    .then(suggestions => {
                        awesomplete.list = suggestions.map(function(suggestion) {
                            return {
                                label: suggestion.stop_name,
                                value: suggestion.stop_id
                            };
                        });
                    });
            }
        });

        stopNameInput.addEventListener('awesomplete-selectcomplete', function(event) {
            stopIdInput.value = event.text.value;
        });

        // Add form submit handler
        document.querySelector('form').addEventListener('submit', function(event) {
            const stopId = stopIdInput.value;
            if (stopId) {
                const newUrl = `${window.location.pathname}?stop_id=${stopId}`;
                window.history.pushState({ stopId: stopId }, '', newUrl);
            }
        });

        // Handle browser back/forward buttons
        window.onpopstate = function(event) {
            const urlParams = new URLSearchParams(window.location.search);
            const stopId = urlParams.get('stop_id');
            if (stopId) {
                stopIdInput.value = stopId;
                const autoRefreshElement = document.getElementById('auto-refresh');
                if (autoRefreshElement) {
                    htmx.trigger('#auto-refresh', 'refresh');
                }
            }
        };

        // Check URL parameters on page load
        const urlParams = new URLSearchParams(window.location.search);
        const stopId = urlParams.get('stop_id');
        if (stopId) {
            stopIdInput.value = stopId;
            const autoRefreshElement = document.getElementById('auto-refresh');
            if (autoRefreshElement) {
                htmx.trigger('#auto-refresh', 'refresh');
            }
        }
    }

    // Service Worker registration
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/service-worker.js').then(function(registration) {
            console.log('Service Worker registered with scope:', registration.scope);
        }).catch(function(error) {
            console.error('Service Worker registration failed:', error);
        });
    }

    // Handle HTMX request errors
    document.body.addEventListener('htmx:responseError', function(event) {
        console.error('HTMX request failed:', event.detail);
        event.preventDefault();
        const errorDiv = document.getElementById('error-message');
        if (errorDiv) {
            errorDiv.textContent = 'Error fetching bus times. Retrying...';
            setTimeout(function() {
                errorDiv.textContent = '';
            }, 5000);
        }
        setTimeout(function() {
            htmx.trigger('#auto-refresh', 'refresh');
        }, 5000);
    });
});