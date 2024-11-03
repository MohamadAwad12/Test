# Initialize eventlet monkey patch before other imports
import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template
import requests
import time
from flask_socketio import SocketIO, emit
import logging
from threading import Thread

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# Configuration
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

def get_token_price(token_address):
    """Fetch token price from DEX Screener"""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        logger.info(f"Fetching price for {token_address}")
        
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            logger.info(f"DEX Screener response received for {token_address}")
            
            if data and 'pairs' in data and len(data['pairs']) > 0:
                # Get the first Raydium pair (usually the main pool)
                raydium_pairs = [p for p in data['pairs'] if p['dexId'] == 'raydium']
                
                if raydium_pairs:
                    pair = raydium_pairs[0]  # Use the first Raydium pair
                else:
                    pair = data['pairs'][0]  # Fallback to first available pair
                
                price = float(pair['priceUsd'])
                priceChange = pair.get('priceChange', {}).get('h24', 0)
                volume24h = float(pair.get('volume', {}).get('h24', 0))
                
                logger.info(f"Price found: ${price:.8f} | 24h Change: {priceChange}% | 24h Volume: ${volume24h:.2f}")
                
                return {
                    'price': price,
                    'priceChange24h': priceChange,
                    'volume24h': volume24h,
                    'liquidity': float(pair.get('liquidity', {}).get('usd', 0)),
                    'fdv': float(pair.get('fdv', 0)),
                    'dexId': pair.get('dexId', ''),
                    'pairAddress': pair.get('pairAddress', '')
                }
            else:
                logger.error(f"No pairs found for {token_address}")
                
        else:
            logger.error(f"Error response from DEX Screener: {response.status_code}")
            
    except Exception as e:
        logger.error(f"Error fetching price: {str(e)}")
    
    # Return default values if anything fails
    return {
        'price': 0,
        'priceChange24h': 0,
        'volume24h': 0,
        'liquidity': 0,
        'fdv': 0,
        'dexId': 'unknown',
        'pairAddress': ''
    }

def update_prices():
    """Update all token prices"""
    while True:
        try:
            prices = {}
            total_value = 0
            
            for token_name, token_info in TOKENS.items():
                address = token_info['address']
                holdings = token_info['holdings']
                
                # Get price and market data
                market_data = get_token_price(address)
                price = market_data['price']
                value = price * holdings
                total_value += value
                
                prices[token_name] = {
                    'price': price,
                    'holdings': holdings,
                    'value': value,
                    'priceChange24h': market_data['priceChange24h'],
                    'volume24h': market_data['volume24h'],
                    'liquidity': market_data['liquidity'],
                    'fdv': market_data['fdv'],
                    'dexId': market_data['dexId'],
                    'pairAddress': market_data['pairAddress'],
                    'timestamp': time.time()
                }
                
                logger.info(f"{token_name}: Price=${price:.8f}, Value=${value:.2f}")
            
            market_data = {
                'prices': prices,
                'total_value': total_value
            }
            
            logger.info(f"Broadcasting update to clients")
            socketio.emit('price_update', market_data)
            
        except Exception as e:
            logger.error(f"Update error: {str(e)}")
        
        time.sleep(3)

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    logger.info('Client connected')
    emit('status', {'message': 'Connected to price feed'})

@socketio.on('disconnect')
def handle_disconnect():
    logger.info('Client disconnected')

if __name__ == '__main__':
    logger.info("Starting price tracker...")
    
    # Start price update thread
    price_thread = Thread(target=update_prices)
    price_thread.daemon = True
    price_thread.start()
    
    # Run the application
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port)
