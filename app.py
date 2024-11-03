# Initialize eventlet monkey patch before other imports
import eventlet
eventlet.monkey_patch()

import os
import time
import logging
import requests
import json
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
from threading import Thread
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask and SocketIO
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'default_secret_key')
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='eventlet',
    logger=True,
    engineio_logger=True,
    ping_timeout=60,
    ping_interval=25
)

# Token configuration
TOKENS = {
    'PONKE': {
        'address': '5z3EqYQo9HiCEs3R84RCDMu2n7anpDMxRhdK8PSWmrRC',
        'holdings': 1000000
    },
    'GME': {
        'address': '8wXtPeU6557ETkp9WHFY1n1EcU6NxDvbAggHGsMYiHsB',
        'holdings': 1000000
    },
    'USA': {
        'address': '69kdRLyP5DTRkpHraaSZAQbWmAwzF9guKjZfzMXzcbAs',
        'holdings': 1000000
    }
}

class PriceTracker:
    @staticmethod
    def get_dexscreener_price(token_address):
        """
        Get token price from DEXScreener with enhanced reliability
        """
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

        try:
            base_url = "https://api.dexscreener.com/latest/dex/tokens"
            url = f"{base_url}/{token_address}"
            
            logger.info(f"Fetching DEXScreener data for {token_address}")
            response = session.get(url, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            
            if not data.get('pairs'):
                logger.warning(f"No pairs found for {token_address}")
                return None
                
            # Sort pairs by liquidity to get the most liquid pair
            pairs = sorted(
                [p for p in data['pairs'] if p.get('liquidity', {}).get('usd', 0) > 0],
                key=lambda x: float(x.get('liquidity', {}).get('usd', 0)),
                reverse=True
            )
            
            if not pairs:
                logger.warning(f"No valid pairs found for {token_address}")
                return None
                
            # Get the most liquid pair
            pair = pairs[0]
            
            return {
                'price': float(pair.get('priceUsd', 0)),
                'priceChange24h': float(pair.get('priceChange', {}).get('h24', 0)),
                'volume24h': float(pair.get('volume', {}).get('h24', 0)),
                'liquidity': float(pair.get('liquidity', {}).get('usd', 0)),
                'dexId': pair.get('dexId', ''),
                'pairAddress': pair.get('pairAddress', '')
            }
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed for {token_address}: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Error processing data for {token_address}: {str(e)}")
            return None

    @staticmethod
    def update_prices():
        """
        Continuous price update loop with retries
        """
        while True:
            try:
                prices = {}
                total_value = 0
                update_successful = False

                for token_name, token_info in TOKENS.items():
                    max_retries = 3
                    retry_delay = 2
                    
                    for attempt in range(max_retries):
                        try:
                            address = token_info['address']
                            holdings = token_info['holdings']

                            logger.info(f"Fetching data for {token_name} (Attempt {attempt + 1}/{max_retries})")
                            market_data = PriceTracker.get_dexscreener_price(address)
                            
                            if market_data is None:
                                if attempt < max_retries - 1:
                                    logger.info(f"Retrying {token_name} in {retry_delay} seconds...")
                                    time.sleep(retry_delay)
                                    continue
                                logger.error(f"All retries failed for {token_name}")
                                break
                            
                            price = market_data['price']
                            value = price * holdings
                            total_value += value
                            update_successful = True

                            prices[token_name] = {
                                'price': price,
                                'holdings': holdings,
                                'value': value,
                                'priceChange24h': market_data['priceChange24h'],
                                'volume24h': market_data['volume24h'],
                                'liquidity': market_data['liquidity'],
                                'dexId': market_data['dexId'],
                                'pairAddress': market_data['pairAddress'],
                                'timestamp': time.time()
                            }

                            logger.info(f"{token_name}: Price=${price:.8f}, Value=${value:.2f}")
                            break  # Success, exit retry loop

                        except Exception as e:
                            logger.error(f"Error processing {token_name}: {str(e)}")
                            if attempt < max_retries - 1:
                                time.sleep(retry_delay)
                                continue
                            break

                if update_successful:
                    market_data = {
                        'prices': prices,
                        'total_value': total_value
                    }

                    logger.info(f"Broadcasting update. Total value: ${total_value:.2f}")
                    socketio.emit('price_update', market_data)
                else:
                    logger.error("No valid prices fetched in this cycle")

            except Exception as e:
                logger.error(f"Update loop error: {str(e)}")

            finally:
                # Add random delay between 3-5 seconds to avoid rate limiting
                delay = 3 + (hash(str(time.time())) % 3)
                time.sleep(delay)

# Route handlers
@app.route('/')
def index():
    """Render the main page"""
    return render_template('index.html')

# Socket event handlers
@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    logger.info('Client connected')
    emit('status', {'message': 'Connected to price feed'})

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    logger.info('Client disconnected')

def initialize_app():
    """Initialize the application and start the price tracker"""
    try:
        logger.info("Initializing price tracker...")
        
        # Start price update thread
        price_thread = Thread(target=PriceTracker.update_prices)
        price_thread.daemon = True
        price_thread.start()
        
        logger.info("Price tracker initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize application: {str(e)}")
        return False

if __name__ == '__main__':
    # Initialize the application
    if initialize_app():
        # Get port from environment variable or use default
        port = int(os.environ.get('PORT', 5000))
        
        # Run the application
        logger.info(f"Starting server on port {port}")
        socketio.run(
            app,
            host='0.0.0.0',
            port=port,
            debug=False,
            use_reloader=False
        )
    else:
        logger.error("Application failed to initialize")
