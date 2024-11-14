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

    // Initialize favourites manager at the end
    window.favouritesManager = new FavouritesManager();
});

class FavouritesManager {
    constructor() {
        this.favourites = this.loadFavourites();
        this.initializeEventListeners();
        this.renderFavourites();
    }

    loadFavourites() {
        const stored = localStorage.getItem('busStopFavourites');
        return stored ? JSON.parse(stored) : {};
    }

    saveFavourites() {
        localStorage.setItem('busStopFavourites', JSON.stringify(this.favourites));
    }

    toggleFavourite(stopId, stopName) {
        if (this.favourites[stopId]) {
            delete this.favourites[stopId];
        } else {
            this.favourites[stopId] = stopName;
        }
        this.saveFavourites();
        this.renderFavourites();
        this.updateFavouriteButtons();
    }

    renderFavourites() {
        const favouritesList = document.getElementById('favourites-list');
        if (!favouritesList) return;

        favouritesList.innerHTML = '';
        
        Object.entries(this.favourites).forEach(([stopId, stopName]) => {
            const card = document.createElement('div');
            card.className = 'favourite-card';
            card.innerHTML = `
                <h3>${stopName}</h3>
                <button class="favourite-button active" data-stop-id="${stopId}" title="Remove from favourites">
                    <i class="fas fa-star"></i>
                </button>
            `;
            
            card.addEventListener('click', (e) => {
                if (e.target.closest('.favourite-button')) {
                    e.preventDefault();
                    e.stopPropagation();
                    this.toggleFavourite(stopId, stopName);
                } else {
                    const newUrl = `${window.location.pathname}?stop_id=${stopId}`;
                    window.history.pushState({ stopId: stopId }, '', newUrl);
                    
                    const stopIdInput = document.getElementById('stop_id');
                    if (stopIdInput) {
                        stopIdInput.value = stopId;
                        const autoRefreshElement = document.getElementById('auto-refresh');
                        if (autoRefreshElement) {
                            htmx.trigger('#auto-refresh', 'refresh');
                        }
                    }
                }
            });
            
            favouritesList.appendChild(card);
        });

        const favouritesSection = document.getElementById('favourites-section');
        if (favouritesSection) {
            favouritesSection.style.display = Object.keys(this.favourites).length ? 'block' : 'none';
        }
    }

    updateFavouriteButtons() {
        document.querySelectorAll('.favourite-button').forEach(button => {
            const stopId = button.dataset.stopId;
            button.classList.toggle('active', !!this.favourites[stopId]);
            button.innerHTML = `<i class="fas fa-star"></i>`;
        });
    }

    initializeEventListeners() {
        document.body.addEventListener('htmx:afterSwap', (event) => {
            if (event.detail.target.id === 'bus-times') {
                this.addFavouriteButtonToBusTimes();
            }
        });
    }

    addFavouriteButtonToBusTimes() {
        const busTimesHeader = document.querySelector('#bus-times h2');
        if (!busTimesHeader) return;

        const stopName = busTimesHeader.textContent.replace('Upcoming Buses at ', '').trim();
        const stopId = new URLSearchParams(window.location.search).get('stop_id');
        
        if (!stopId) return;

        if (!document.querySelector('.favourite-button')) {
            const favouriteButton = document.createElement('button');
            favouriteButton.className = `favourite-button ${this.favourites[stopId] ? 'active' : ''}`;
            favouriteButton.dataset.stopId = stopId;
            favouriteButton.title = this.favourites[stopId] ? 'Remove from favourites' : 'Add to favourites';
            favouriteButton.innerHTML = `<i class="fas fa-star"></i>`;
            
            favouriteButton.addEventListener('click', () => this.toggleFavourite(stopId, stopName));
            
            busTimesHeader.appendChild(favouriteButton);
        }
    }
}
