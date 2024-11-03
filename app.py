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

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,  # Set to DEBUG for more verbose logs
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
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
    ping_interval=25,
    max_http_buffer_size=1e8
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
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        self.last_prices = {}
        
    def get_dexscreener_price(self, token_address):
        """
        Get token price from DEXScreener with enhanced reliability
        """
        try:
            base_url = "https://api.dexscreener.com/latest/dex/tokens"
            url = f"{base_url}/{token_address}"
            
            logger.info(f"[DEXScreener] Fetching data for {token_address}")
            logger.debug(f"[DEXScreener] Request URL: {url}")
            
            # Log the request headers
            logger.debug(f"[DEXScreener] Request headers: {dict(self.session.headers)}")
            
            response = self.session.get(url, timeout=15)
            
            # Log the response status and headers
            logger.debug(f"[DEXScreener] Response status: {response.status_code}")
            logger.debug(f"[DEXScreener] Response headers: {dict(response.headers)}")
            
            # Log the raw response
            logger.debug(f"[DEXScreener] Raw response: {response.text[:500]}...")  # First 500 chars
            
            response.raise_for_status()
            data = response.json()
            
            if not data.get('pairs'):
                logger.warning(f"[DEXScreener] No pairs found for {token_address}")
                # Return last known price if available
                if token_address in self.last_prices:
                    logger.info(f"[DEXScreener] Using last known price for {token_address}")
                    return self.last_prices[token_address]
                return None
            
            # Filter for valid pairs and sort by liquidity
            valid_pairs = [
                p for p in data['pairs']
                if (p.get('liquidity', {}).get('usd', 0) > 0 and
                    p.get('priceUsd') and
                    float(p.get('priceUsd', 0)) > 0)
            ]
            
            if not valid_pairs:
                logger.warning(f"[DEXScreener] No valid pairs found for {token_address}")
                return None
            
            # Sort by liquidity
            valid_pairs.sort(key=lambda x: float(x.get('liquidity', {}).get('usd', 0)), reverse=True)
            pair = valid_pairs[0]
            
            price_data = {
                'price': float(pair['priceUsd']),
                'priceChange24h': float(pair.get('priceChange', {}).get('h24', 0)),
                'volume24h': float(pair.get('volume', {}).get('h24', 0)),
                'liquidity': float(pair.get('liquidity', {}).get('usd', 0)),
                'dexId': pair.get('dexId', ''),
                'pairAddress': pair.get('pairAddress', '')
            }
            
            # Store the last known good price
            self.last_prices[token_address] = price_data
            
            logger.info(f"[DEXScreener] Successfully fetched price for {token_address}: ${price_data['price']:.8f}")
            return price_data
            
        except requests.exceptions.RequestException as e:
            logger.error(f"[DEXScreener] Request failed for {token_address}: {str(e)}")
            # Return last known price if available
            if token_address in self.last_prices:
                logger.info(f"[DEXScreener] Using last known price for {token_address}")
                return self.last_prices[token_address]
            return None
            
        except Exception as e:
            logger.error(f"[DEXScreener] Error processing data for {token_address}: {str(e)}")
            logger.exception("Detailed traceback:")
            return None

    def update_prices(self):
        """
        Continuous price update loop with retries and fallback
        """
        retry_delays = [2, 4, 8]  # Progressive retry delays
        
        while True:
            try:
                prices = {}
                total_value = 0
                update_successful = False
                successful_tokens = set()

                for token_name, token_info in TOKENS.items():
                    logger.info(f"Processing {token_name}")
                    
                    for attempt, delay in enumerate(retry_delays, 1):
                        try:
                            address = token_info['address']
                            holdings = token_info['holdings']

                            logger.info(f"Fetching data for {token_name} (Attempt {attempt}/{len(retry_delays)})")
                            market_data = self.get_dexscreener_price(address)
                            
                            if market_data is None:
                                if attempt < len(retry_delays):
                                    logger.info(f"Retrying {token_name} in {delay} seconds...")
                                    time.sleep(delay)
                                    continue
                                logger.error(f"All retries failed for {token_name}")
                                break
                            
                            price = market_data['price']
                            value = price * holdings
                            total_value += value
                            update_successful = True
                            successful_tokens.add(token_name)

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

                            logger.info(f"Successfully updated {token_name}: Price=${price:.8f}, Value=${value:.2f}")
                            break  # Success, exit retry loop

                        except Exception as e:
                            logger.error(f"Error processing {token_name} on attempt {attempt}: {str(e)}")
                            if attempt < len(retry_delays):
                                time.sleep(delay)
                                continue
                            break

                if update_successful:
                    market_data = {
                        'prices': prices,
                        'total_value': total_value
                    }

                    logger.info(f"Broadcasting update. Total value: ${total_value:.2f}")
                    logger.info(f"Successfully updated tokens: {', '.join(successful_tokens)}")
                    socketio.emit('price_update', market_data)
                else:
                    logger.error("No valid prices fetched in this cycle")

            except Exception as e:
                logger.error(f"Update loop error: {str(e)}")
                logger.exception("Detailed traceback:")

            finally:
                # Random delay between 3-5 seconds
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
    logger.info('[WebSocket] Client connected')
    emit('status', {'message': 'Connected to price feed'})

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    logger.info('[WebSocket] Client disconnected')

def initialize_app():
    """Initialize the application and start the price tracker"""
    try:
        logger.info("Initializing price tracker...")
        
        # Create price tracker instance
        price_tracker = PriceTracker()
        
        # Start price update thread
        price_thread = Thread(target=price_tracker.update_prices)
        price_thread.daemon = True
        price_thread.start()
        
        logger.info("Price tracker initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize application: {str(e)}")
        logger.exception("Detailed traceback:")
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
