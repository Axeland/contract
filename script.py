import time
import json
import logging
import os
from typing import Dict, Any, Optional, List

import requests
from web3 import Web3
from web3.exceptions import ContractLogicError, TransactionNotFound
from web3.contract import Contract
from web3.types import LogReceipt
from tenacity import retry, stop_after_attempt, wait_exponential

# --- Configuration Setup ---
# In a real-world application, this would be loaded from a config file (e.g., .env, config.yaml)
CONFIG = {
    'source_chain': {
        'name': 'Ethereum_Sepolia',
        'rpc_url': 'https://rpc.sepolia.org', # Replace with your own RPC node URL
        'chain_id': 11155111,
        'bridge_contract_address': '0x779877A7B0D9E8603169DdbD7836e478b4624789', # Example: Chainlink Token on Sepolia
        'etherscan_api_key': 'YOUR_ETHERSCAN_API_KEY' # Replace with your key to fetch ABI
    },
    'destination_chain': {
        'name': 'Polygon_Mumbai',
        'rpc_url': 'https://rpc-mumbai.maticvigil.com', # Replace with your own RPC node URL
        'chain_id': 80001,
        'bridge_contract_address': '0x...', # Placeholder for the destination contract
        'private_key': '0x...' # IMPORTANT: Never hardcode private keys in production!
    },
    'event_to_listen': 'Transfer',
    'poll_interval_seconds': 15,
    'state_db_file': 'processed_events_db.json'
}

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(name)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('BridgeListener')


class StateDB:
    """Manages the state of processed events to prevent duplicates."""

    def __init__(self, db_path: str):
        """
        Initializes the StateDB.
        Args:
            db_path (str): The file path for the persistent state database.
        """
        self.db_path = db_path
        self.processed_txs = self._load_state()
        logger.info(f"StateDB initialized. Loaded {len(self.processed_txs)} processed transaction hashes.")

    def _load_state(self) -> Dict[str, bool]:
        """Loads the state from a JSON file."""
        if not os.path.exists(self.db_path):
            return {}
        try:
            with open(self.db_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Error loading state from {self.db_path}: {e}. Starting with an empty state.")
            return {}

    def _save_state(self):
        """Saves the current state to the JSON file."""
        try:
            with open(self.db_path, 'w') as f:
                json.dump(self.processed_txs, f, indent=4)
        except IOError as e:
            logger.error(f"Could not save state to {self.db_path}: {e}")

    def is_processed(self, tx_hash: str) -> bool:
        """Checks if a transaction hash has already been processed."""
        return tx_hash in self.processed_txs

    def mark_as_processed(self, tx_hash: str):
        """
        Marks a transaction hash as processed and saves the state.
        Args:
            tx_hash (str): The transaction hash to mark.
        """
        self.processed_txs[tx_hash] = True
        self._save_state()
        logger.info(f"Transaction {tx_hash} marked as processed.")


class BlockchainConnector:
    """Handles the connection to a blockchain via a Web3 provider."""

    def __init__(self, chain_name: str, rpc_url: str):
        """
        Initializes the blockchain connector.
        Args:
            chain_name (str): The name of the chain for logging purposes.
            rpc_url (str): The HTTP/S or WSS RPC endpoint URL.
        """
        self.chain_name = chain_name
        self.rpc_url = rpc_url
        self.web3: Optional[Web3] = None
        self.connect()

    @retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=4, max=10))
    def connect(self):
        """Establishes a connection to the blockchain node with retry logic."""
        logger.info(f"Attempting to connect to {self.chain_name} via {self.rpc_url}...")
        try:
            self.web3 = Web3(Web3.HTTPProvider(self.rpc_url))
            if not self.web3.is_connected():
                raise ConnectionError(f"Failed to connect to {self.chain_name}.")
            logger.info(f"Successfully connected to {self.chain_name}. Chain ID: {self.web3.eth.chain_id}")
        except Exception as e:
            logger.error(f"Connection error for {self.chain_name}: {e}")
            raise

    def get_web3(self) -> Web3:
        """Returns the active Web3 instance, ensuring it's connected."""
        if not self.web3 or not self.web3.is_connected():
            logger.warning(f"Connection to {self.chain_name} lost. Reconnecting...")
            self.connect()
        return self.web3


class ContractEventHandler:
    """Manages interaction with a specific smart contract for event listening."""

    def __init__(self, connector: BlockchainConnector, contract_address: str, api_key: str):
        """
        Initializes the event handler.
        Args:
            connector (BlockchainConnector): The connector for the blockchain.
            contract_address (str): The address of the smart contract to monitor.
            api_key (str): Etherscan API key to fetch the ABI.
        """
        self.connector = connector
        self.web3 = self.connector.get_web3()
        self.address = Web3.to_checksum_address(contract_address)
        self.abi = self._fetch_abi()
        if not self.abi:
            raise ValueError("Failed to fetch ABI. Cannot initialize contract.")
        self.contract: Contract = self.web3.eth.contract(address=self.address, abi=self.abi)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=6))
    def _fetch_abi(self) -> Optional[List[Dict[str, Any]]]:
        """Fetches the contract ABI from a block explorer API (like Etherscan)."""
        chain_id = self.web3.eth.chain_id
        # Map chain ID to Etherscan API subdomain
        api_endpoints = {
            1: 'api.etherscan.io',
            11155111: 'api-sepolia.etherscan.io',
            80001: 'api-testnet.polygonscan.com'
        }
        api_host = api_endpoints.get(chain_id)
        if not api_host:
            logger.error(f"No Etherscan API endpoint configured for chain ID {chain_id}")
            return None
        
        api_url = f'https://{api_host}/api?module=contract&action=getabi&address={self.address}&apikey={api_key}'
        logger.info(f"Fetching ABI for {self.address} from {api_host}...")
        try:
            response = requests.get(api_url)
            response.raise_for_status()
            data = response.json()
            if data['status'] == '1':
                logger.info(f"Successfully fetched ABI for {self.address}")
                return json.loads(data['result'])
            else:
                logger.error(f"Failed to fetch ABI: {data['message']} - {data['result']}")
                return None
        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP error while fetching ABI: {e}")
            return None

    def get_events(self, event_name: str, from_block: int, to_block: int) -> List[LogReceipt]:
        """Fetches specific events from the contract within a block range."""
        try:
            event = getattr(self.contract.events, event_name)
            event_filter = event.create_filter(fromBlock=from_block, toBlock=to_block)
            return event_filter.get_all_entries()
        except Exception as e:
            logger.error(f"Error fetching events for '{event_name}': {e}")
            return []


class TransactionProcessor:
    """Simulates the processing of a cross-chain event and transaction submission."""

    def __init__(self, connector: BlockchainConnector, private_key: str):
        """
        Initializes the transaction processor.
        Args:
            connector (BlockchainConnector): Connector for the destination chain.
            private_key (str): Private key for signing transactions on the destination chain.
        """
        self.connector = connector
        self.web3 = connector.get_web3()
        self.private_key = private_key
        # In a real scenario, you would derive the account from the private key.
        # self.account = self.web3.eth.account.from_key(private_key)

    def process_bridge_event(self, event: LogReceipt):
        """Simulates the processing of a detected bridge event."""
        tx_hash = event['transactionHash'].hex()
        event_args = event['args']
        logger.info(f"Processing event from transaction {tx_hash}...")
        logger.info(f"Event data: from={event_args['from']}, to={event_args['to']}, value={event_args['value']}")

        # --- Simulation of Destination Chain Transaction ---
        # In a real implementation, you would:
        # 1. Connect to the destination chain's bridge contract.
        # 2. Construct a function call (e.g., 'unlockTokens' or 'mint').
        # 3. Build the transaction with nonce, gas price, etc.
        # 4. Sign the transaction with the private key.
        # 5. Send the raw transaction.
        # 6. Wait for the transaction receipt and handle potential failures.

        logger.info(f"[SIMULATION] Building transaction for destination chain: {self.connector.chain_name}")
        logger.info(f"[SIMULATION] To Contract: {CONFIG['destination_chain']['bridge_contract_address']}")
        logger.info(f"[SIMULATION] Function: unlockTokens(to='{event_args['to']}', amount={event_args['value']}) ")
        logger.info(f"[SIMULATION] Original source tx_hash for proof: {tx_hash}")
        logger.info(f"[SIMULATION] Transaction has been successfully processed and sent to destination chain.")

        # Simulate a delay for processing
        time.sleep(2)
        return True


class CrossChainBridgeListener:
    """The main orchestrator for listening to events and processing them."""

    def __init__(self, config: Dict[str, Any]):
        """
        Initializes the main listener.
        Args:
            config (Dict[str, Any]): The global configuration dictionary.
        """
        self.config = config
        self.state_db = StateDB(config['state_db_file'])
        
        logger.info("Initializing blockchain connectors...")
        self.source_connector = BlockchainConnector(
            config['source_chain']['name'],
            config['source_chain']['rpc_url']
        )
        self.destination_connector = BlockchainConnector(
            config['destination_chain']['name'],
            config['destination_chain']['rpc_url']
        )

        logger.info("Initializing contract event handler for the source chain...")
        self.source_event_handler = ContractEventHandler(
            self.source_connector,
            config['source_chain']['bridge_contract_address'],
            config['source_chain']['etherscan_api_key']
        )

        logger.info("Initializing transaction processor for the destination chain...")
        self.tx_processor = TransactionProcessor(
            self.destination_connector,
            config['destination_chain']['private_key']
        )

        self.last_processed_block = self._get_starting_block()

    def _get_starting_block(self) -> int:
        """Determines the block to start scanning from."""
        # In a production system, this would be loaded from persistent storage.
        # For this simulation, we start from the latest block.
        try:
            latest_block = self.source_connector.get_web3().eth.block_number
            logger.info(f"Starting scan from the latest block: {latest_block}")
            return latest_block
        except Exception as e:
            logger.error(f"Could not fetch the latest block number. Exiting. Error: {e}")
            exit(1)

    def run(self):
        """Starts the main event listening loop."""
        logger.info("Cross-chain bridge listener started. Waiting for new events...")
        try:
            while True:
                self._poll_for_events()
                time.sleep(self.config['poll_interval_seconds'])
        except KeyboardInterrupt:
            logger.info("Shutdown signal received. Exiting gracefully.")
        except Exception as e:
            logger.critical(f"An unhandled error occurred in the main loop: {e}", exc_info=True)

    def _poll_for_events(self):
        """Polls the source chain for new events since the last processed block."""
        try:
            web3 = self.source_connector.get_web3()
            current_block = web3.eth.block_number

            # Don't scan the most recent block to avoid issues with chain reorgs.
            # A confirmation delay of 5-6 blocks is recommended for production.
            scan_to_block = current_block - 1

            if scan_to_block <= self.last_processed_block:
                logger.debug(f"No new blocks to scan. Current: {current_block}, Last Scanned: {self.last_processed_block}")
                return

            logger.info(f"Scanning for '{self.config['event_to_listen']}' events from block {self.last_processed_block + 1} to {scan_to_block}...")
            
            events = self.source_event_handler.get_events(
                event_name=self.config['event_to_listen'],
                from_block=self.last_processed_block + 1,
                to_block=scan_to_block
            )

            if events:
                logger.info(f"Found {len(events)} new event(s).")
                for event in events:
                    self._handle_event(event)
            else:
                logger.info("No new events found in this range.")

            # Update the last processed block *after* successfully scanning the range
            self.last_processed_block = scan_to_block

        except Exception as e:
            logger.error(f"Error during event polling loop: {e}")
            # We don't update last_processed_block on error to ensure the range is retried

    def _handle_event(self, event: LogReceipt):
        """Handles a single detected event, including validation and processing."""
        tx_hash = event['transactionHash'].hex()
        log_index = event['logIndex']
        unique_event_id = f"{tx_hash}-{log_index}"

        if self.state_db.is_processed(unique_event_id):
            logger.warning(f"Event {unique_event_id} has already been processed. Skipping.")
            return

        logger.info(f"New unprocessed event found: {unique_event_id}")

        # --- Additional Validation Logic --- 
        # Here you could add more checks, e.g.:
        # - Check if the event's destinationChainId matches our destination chain.
        # - Check if the amount is within acceptable limits.
        # if event['args']['destinationChainId'] != self.config['destination_chain']['chain_id']:
        #     logger.warning(f"Skipping event for wrong destination chain ID.")
        #     return

        try:
            success = self.tx_processor.process_bridge_event(event)
            if success:
                self.state_db.mark_as_processed(unique_event_id)
            else:
                logger.error(f"Failed to process event {unique_event_id}. It will be retried later.")
        except Exception as e:
            logger.error(f"An exception occurred while processing event {unique_event_id}: {e}", exc_info=True)


if __name__ == '__main__':
    # Basic validation of the configuration
    if CONFIG['source_chain']['etherscan_api_key'] == 'YOUR_ETHERSCAN_API_KEY':
        logger.error("Please replace 'YOUR_ETHERSCAN_API_KEY' in the CONFIG dictionary.")
        exit(1)
    if CONFIG['destination_chain']['private_key'] == '0x...':
        logger.warning("Using a placeholder private key for simulation. No real transactions will be sent.")

    # Instantiate and run the listener
    listener = CrossChainBridgeListener(CONFIG)
    listener.run()

# @-internal-utility-start
def log_event_1000(event_name: str, level: str = "INFO"):
    """Logs a system event - added on 2025-11-03 13:49:14"""
    print(f"[{level}] - 2025-11-03 13:49:14 - Event: {event_name}")
# @-internal-utility-end


# @-internal-utility-start
def format_timestamp_3717(ts: float):
    """Formats a unix timestamp into ISO format. Updated on 2025-11-15 17:58:21"""
    import datetime
    dt_object = datetime.datetime.fromtimestamp(ts)
    return dt_object.isoformat()
# @-internal-utility-end

