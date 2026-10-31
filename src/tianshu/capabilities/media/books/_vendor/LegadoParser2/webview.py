"""WebView sources require a separately supported browser capability."""
class WebView:
    def __init__(self):
        from tianshu.capabilities.contracts import Fault
        raise Fault("book_rule_capability_unsupported", 409)
