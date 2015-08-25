from itertools import count
import json
import pytest
from devp2p.peermanager import PeerManager
from ethereum import tester
from ethereum.ethpow import mine
from ethereum.slogging import get_logger
from pyethapp.accounts import Account, AccountsService, mk_random_privkey
from pyethapp.app import EthApp
from pyethapp.config import update_config_with_defaults, get_default_config
from pyethapp.db_service import DBService
from pyethapp.eth_service import ChainService
from pyethapp.jsonrpc import JSONRPCServer, quantity_encoder, address_encoder, data_decoder
from pyethapp.pow_service import PoWService

log = get_logger('test.jsonrpc')


solidity_code = "contract test { function multiply(uint a) returns(uint d) {   return a * 7;   } }"
def test_compileSolidity():
    from pyethapp.jsonrpc import Compilers, data_encoder
    import ethereum._solidity
    s = ethereum._solidity.get_solidity()
    if s == None:
        pytest.xfail("solidity not installed, not tested")
    else:
        c = Compilers()
        bc = s.compile(solidity_code)
        abi = s.mk_full_signature(solidity_code)
        r = dict(code=data_encoder(bc),
             info=dict(source=solidity_code,
                       language='Solidity',
                       languageVersion='0',
                       compilerVersion='0',
                       abiDefinition=abi,
                       userDoc=dict(methods=dict()),
                       developerDoc=dict(methods=dict()),
                       )
             )
        assert r == c.compileSolidity(solidity_code)


@pytest.fixture
def test_app(request, tmpdir):

    class TestApp(EthApp):

        def start(self):
            super(TestApp, self).start()
            log.debug('adding test accounts')
            # high balance account
            self.services.accounts.add_account(Account.new('', tester.keys[0]), store=False)
            # low balance account
            self.services.accounts.add_account(Account.new('', tester.keys[1]), store=False)
            # locked account
            locked_account = Account.new('', tester.keys[2])
            locked_account.lock()
            self.services.accounts.add_account(locked_account, store=False)
            assert set(acct.address for acct in self.services.accounts) == set(tester.accounts[:3])

        def mine_next_block(self):
            """Mine until a valid nonce is found."""
            log.debug('mining next block')
            block = self.services.chain.chain.head_candidate
            delta_nonce = 10**6
            for start_nonce in count(0, delta_nonce):
                bin_nonce, mixhash = mine(block.number, block.difficulty, block.mining_hash,
                                          start_nonce=start_nonce, rounds=delta_nonce)
                if bin_nonce:
                    break
            self.services.pow.recv_found_nonce(bin_nonce, mixhash, block.mining_hash)
            log.debug('block mined')

        def rpc_request(self, method, *args):
            """Simulate an incoming JSON RPC request and return the result.

            Example::

                >>> assert test_app.rpc_request('eth_getBalance', '0x' + 'ff' * 20) == '0x0'

            """
            log.debug('simulating rpc request', method=method)
            method = self.services.jsonrpc.dispatcher.get_method(method)
            res = method(*args)
            log.debug('got response', response=res)
            return res

    # genesis block with reduced difficulty, increased gas limit, and allocaitons to test accounts
    genesis_block = {
        "nonce": "0x0000000000000042",
        "difficulty": "0x1",
        "alloc": {
            tester.accounts[0].encode('hex'): {'balance': 10**24},
            tester.accounts[1].encode('hex'): {'balance': 1},
            tester.accounts[2].encode('hex'): {'balance': 10**24},
        },
        "mixhash": "0x0000000000000000000000000000000000000000000000000000000000000000",
        "coinbase": "0x0000000000000000000000000000000000000000",
        "timestamp": "0x00",
        "parentHash": "0x0000000000000000000000000000000000000000000000000000000000000000",
        "extraData": "0x",
        "gasLimit": "0x2fefd8"
    }
    genesis_block_file = tmpdir.join('test_genesis_block.json')
    genesis_block_file.write(json.dumps(genesis_block))

    config = {
        'data_dir': str(tmpdir),
        'db': {'implementation': 'EphemDB'},
        'pow': {'activated': False},
        'p2p': {
            'min_peers': 0,
            'max_peers': 0,
            'listen_port': 29873
        },
        'node': {'privkey_hex': mk_random_privkey().encode('hex')},
        'discovery': {
            'boostrap_nodes': [],
            'listen_port': 29873
        },
        'eth': {'genesis': str(genesis_block_file)},
        'jsonrpc': {'listen_port': 29873}
    }
    services = [DBService, AccountsService, PeerManager, ChainService, PoWService, JSONRPCServer]
    update_config_with_defaults(config, get_default_config([TestApp] + services))
    app = TestApp(config)
    for service in services:
        service.register_with_app(app)

    def fin():
        log.debug('stopping test app')
        app.stop()
    request.addfinalizer(fin)

    log.debug('starting test app')
    app.start()
    return app


def test_send_transaction(test_app):
    chain = test_app.services.chain.chain
    assert chain.head_candidate.get_balance('\xff' * 20) == 0
    tx = {
        'from': address_encoder(test_app.services.accounts.unlocked_accounts()[0].address),
        'to': address_encoder('\xff' * 20),
        'value': quantity_encoder(1)
    }
    tx_hash = data_decoder(test_app.rpc_request('eth_sendTransaction', tx))
    assert tx_hash == chain.head_candidate.get_transaction(0).hash
    assert chain.head_candidate.get_balance('\xff' * 20) == 1
    test_app.mine_next_block()
    assert tx_hash == chain.head.get_transaction(0).hash
    assert chain.head.get_balance('\xff' * 20) == 1

    # send transactions from account which can't pay gas
    tx['from'] = address_encoder(test_app.services.accounts.unlocked_accounts()[1].address)
    tx_hash = data_decoder(test_app.rpc_request('eth_sendTransaction', tx))
    assert chain.head_candidate.get_transactions() == []


def test_pending_transaction_filter(test_app):
    filter_id = test_app.rpc_request('eth_newPendingTransactionFilter')
    assert test_app.rpc_request('eth_getFilterChanges', filter_id) == []
    # single transaction
    tx = {
        'from': address_encoder(test_app.services.accounts.unlocked_accounts()[0].address),
        'to': address_encoder('\xff' * 20)
    }
    tx_hash = test_app.rpc_request('eth_sendTransaction', tx)
    assert test_app.rpc_request('eth_getFilterChanges', filter_id) == [tx_hash]
    assert test_app.rpc_request('eth_getFilterChanges', filter_id) == []

    # multiple transactions
    tx_hashes = set(test_app.rpc_request('eth_sendTransaction', tx) for i in range(3))
    assert set(test_app.rpc_request('eth_getFilterChanges', filter_id)) == tx_hashes
    assert test_app.rpc_request('eth_getFilterChanges', filter_id) == []

    # multiple transactions with new block in between
    tx_hashes = set(test_app.rpc_request('eth_sendTransaction', tx) for i in range(3))
    test_app.mine_next_block()
    tx_hashes |= set(test_app.rpc_request('eth_sendTransaction', tx) for i in range(3))
    assert set(test_app.rpc_request('eth_getFilterChanges', filter_id)) == tx_hashes
    assert test_app.rpc_request('eth_getFilterChanges', filter_id) == []
