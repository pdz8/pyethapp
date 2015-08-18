# pyethapp for CrowdVerity

This is a fork of [ethereum/pyethapp](https://github.com/ethereum/pyethapp) created for the use of [CrowdVerity](https://github.com/pdz8/schelling).
This version of pyethapp is updated regularly from the upstream repository and is fully compatible with the Ethereum network.
Current modifications include:

*   Addition of a `secret` parameter to the `eth_sendTransaction` JSON-RPC command.
    This parameter can be used to specify the private key used to sign the transaction.
    It must agree with `sender`.
