"""Pure command boundary; external work is an output request, never network I/O."""

class ExternalCommandPending(Exception):
    def __init__(self, text):
        self.text = text
        super().__init__('command requires external normalization')


LOGICAL_MESSAGE_BASE = 10**15
