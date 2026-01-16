"""RTBData class for NBELocalConnect."""
from logging import getLogger

_LOGGER = getLogger(__name__)


class RTBData:
    """Class to hold and access all boiler data."""
    
    def __init__(self, data):
        """Initialize with data list."""
        self.data = {}
        self.set(data)
    
    def set(self, data):
        """Update data dictionary from list of key=value strings."""
        if not data:
            _LOGGER.warning("set() called with empty data")
            return
        
        self.data = {}
        
        for item in data:
            if '=' in item:
                # Format: "key=value"
                key, value = item.split('=', 1)
                self.data[key] = value
            else:
                # Format: just "key" (no value) - skip eller gem som None
                _LOGGER.debug(f"Skipping keyless item: {item}")
        
        _LOGGER.debug(f"RTBData updated with {len(self.data)} keys")
    
    def get(self, key):
        """Get value for specific key."""
        value = self.data.get(key)
        
        if value is None:
            _LOGGER.debug(f"Key not found: {key}")
        
        return value
    
    def get_all_starting_with(self, prefix):
        """Get all keys that start with prefix."""
        result = {}
        for key, value in self.data.items():
            if key.startswith(prefix):
                result[key] = value
        return result
    
    def get_all_keys(self):
        """Get list of all available keys."""
        return list(self.data.keys())
    
    def get_all(self):
        """Get entire data dictionary."""
        return self.data
