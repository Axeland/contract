# Cross-Chain Bridge Event Listener

This repository contains a Python script that simulates a critical off-chain component of a cross-chain bridge. It acts as a listener node, monitoring events on a source blockchain (e.g., Ethereum Sepolia) and simulating the initiation of corresponding actions on a destination blockchain (e.g., Polygon Mumbai).

This script is designed to be robust, modular, and illustrative of the architectural patterns used in real-world decentralized systems.

## Concept

A cross-chain bridge allows users to transfer assets or data from one blockchain to another. A common pattern is the "lock-and-mint" mechanism:
1.  A user **locks** an asset (e.g., an ERC20 token) in a smart contract on the source chain.
2.  The smart contract emits an event (e.g., `TokensLocked`) containing details of the transaction.
3.  Off-chain listener nodes (also called validators or relayers) detect this event.
4.  After verifying the event, these nodes submit a transaction to a corresponding smart contract on the destination chain.
5.  The destination contract **mints** a wrapped version of the asset and sends it to the user's address on the new chain.

This script simulates the role of the listener node (Step 3 and 4), providing a framework for handling blockchain events reliably.

## Code Architecture

The script is built with a modular, object-oriented design to separate concerns and enhance maintainability.

```
+----------------------------+
|            main.py         |
|      (Orchestration)       |
+-------------+--------------+
              |
              | Creates & Runs
              v
+----------------------------+
| CrossChainBridgeListener   |
+----------------------------+
| - source_connector         |
| - destination_connector    |  (has a)  +------------------------+
| - source_event_handler     | --------> | ContractEventHandler   |
| - tx_processor             |           +------------------------+
| - state_db                 |           | - connector            |
| - run()                    |           | - contract (Web3)      |
| - _poll_for_events()       |           | - get_events()         |
+----------------------------+           +-----------+------------+
      |            |                                   |
(has a)        (has a)                               (has a)
      |            |                                   |
      v            v                                   v
+----------------+ +---------------------+    +---------------------+
| TransactionProcessor |   |       StateDB       |    | BlockchainConnector |
+----------------+ +---------------------+    +---------------------+
| - connector    |   | - db_path           |    | - web3 instance     |
| - process...() |   | - is_processed()    |    | - connect()         |
+----------------+   | - mark_as_processed() |    +---------------------+
                     +---------------------+
```

-   **`BlockchainConnector`**: A reusable class for managing the connection to any EVM-compatible chain via an RPC endpoint. It uses the `tenacity` library to handle connection retries with exponential backoff, making it resilient to temporary network or node issues.
-   **`ContractEventHandler`**: Manages all interactions with a specific smart contract. It fetches the contract's ABI from a block explorer (like Etherscan) using the `requests` library, creates a `web3.py` contract object, and provides a method to filter for specific events within a given block range.
-   **`StateDB`**: Provides a simple persistence layer. It keeps track of which event transaction hashes have already been processed in a local JSON file. This is crucial for preventing double-spending and ensuring that the listener can be stopped and restarted without reprocessing old events.
-   **`TransactionProcessor`**: Encapsulates the logic for handling a validated event. In this simulation, it logs the details of the transaction that *would* be created and sent to the destination chain. In a real system, this class would handle nonce management, gas estimation, transaction signing, and submission.
-   **`CrossChainBridgeListener`**: The core orchestrator. It initializes all other components, maintains the state of the last scanned block, and runs the main polling loop to check for new events.

## How it Works

1.  **Initialization**: The `CrossChainBridgeListener` is instantiated. It sets up connectors for both the source and destination chains, initializes the state database, and prepares the event handler for the source contract.
2.  **Starting Block**: The listener determines the block number to start scanning from. In this simulation, it simply starts from the latest block to avoid scanning the entire chain history.
3.  **Polling Loop**: The listener enters an infinite loop where it periodically:
    a.  Fetches the current latest block number on the source chain.
    b.  Calculates a safe `to_block` number (a few blocks behind the tip of the chain) to minimize the risk of processing events from blocks that get reorganized.
    c.  Calls the `ContractEventHandler` to query for the target event (e.g., `Transfer`) within the range from `last_processed_block + 1` to `to_block`.
4.  **Event Handling**: If any events are found:
    a.  It iterates through each event.
    b.  It creates a unique ID for the event (based on transaction hash and log index).
    c.  It checks the `StateDB` to see if this event has already been processed. If so, it's skipped.
    d.  If the event is new, it is passed to the `TransactionProcessor`.
5.  **Transaction Simulation**: The `TransactionProcessor` logs the actions it would take to build, sign, and send a transaction to the destination chain to complete the bridge transfer.
6.  **State Update**: After the processor successfully simulates the transaction, the `StateDB` is updated to mark the event as processed. This atomic step ensures that even if the script crashes, the event will not be processed again on restart.
7.  **Loop Continuation**: The `last_processed_block` is updated, and the listener waits for the configured poll interval before starting the next scan.

## Usage Example

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/contract.git
    cd contract
    ```

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure the script:**
    Open `script.py` and modify the `CONFIG` dictionary at the top of the file:
    -   Set `source_chain['rpc_url']` to your personal or a public RPC endpoint for the source network (e.g., from Infura, Alchemy, or Ankr).
    -   Set `source_chain['etherscan_api_key']` to your Etherscan API key. This is needed to fetch the contract ABI automatically.
    -   The script is configured to watch the `Transfer` event on the Chainlink (LINK) token contract on the Sepolia testnet. You can change this to any other contract and event.

4.  **Run the listener:**
    ```bash
    python script.py
    ```

5.  **Observe the output:**
    The script will start logging its status to the console. It will connect to the chains and begin polling for new blocks.

    ```
    2023-10-27 14:30:00 - [INFO] - BridgeListener - StateDB initialized. Loaded 0 processed transaction hashes.
    2023-10-27 14:30:00 - [INFO] - BridgeListener - Initializing blockchain connectors...
    2023-10-27 14:30:01 - [INFO] - BridgeListener - Attempting to connect to Ethereum_Sepolia via https://rpc.sepolia.org...
    2023-10-27 14:30:03 - [INFO] - BridgeListener - Successfully connected to Ethereum_Sepolia. Chain ID: 11155111
    ...
    2023-10-27 14:30:05 - [INFO] - BridgeListener - Starting scan from the latest block: 4580123
    2023-10-27 14:30:05 - [INFO] - BridgeListener - Cross-chain bridge listener started. Waiting for new events...
    2023-10-27 14:30:05 - [INFO] - BridgeListener - Scanning for 'Transfer' events from block 4580124 to 4580125...
    2023-10-27 14:30:08 - [INFO] - BridgeListener - No new events found in this range.
    ```

    When a new `Transfer` event occurs on the target contract, you will see detailed logs about the event being found, processed, and marked as complete in the state database.