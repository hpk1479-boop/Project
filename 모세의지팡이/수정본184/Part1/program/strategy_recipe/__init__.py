"""Public, shared Strategy/Recipe execution API for LIVE and replay."""

def __getattr__(name):
    if name == 'IntentPort':
        from .port import IntentPort
        return IntentPort
    if name == 'IntentMachine':
        from .runtime import IntentMachine
        return IntentMachine
    raise AttributeError(name)
