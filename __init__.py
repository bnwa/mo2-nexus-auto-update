import mobase  # For type-checking createPlugin().

from .plugin import NexusAutoUpdater


def createPlugin() -> mobase.IPlugin:
    return NexusAutoUpdater()
