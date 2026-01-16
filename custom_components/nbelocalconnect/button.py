from homeassistant.core import callback
from homeassistant.helpers.entity import Entity
from homeassistant.components.button import ButtonEntity

from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN
from .protocol import Proxy
from logging import getLogger

_LOGGER = getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    coordinator = hass.data[DOMAIN][config_entry.entry_id+'_coordinator']
    proxy = coordinator.proxy

    async_add_entities([
        RTBSignalButton(coordinator, proxy, "Start Boiler", "settings/misc/start", "nbestart", "1"),
        RTBSignalButton(coordinator, proxy, "Stop Boiler", "settings/misc/stop", "nbestop", "1"),
        RTBSignalButton(coordinator, proxy, "Reset Boiler Alarm", "settings/misc/reset_alarm", "nbereset", "1")
    ])

class RTBSignalButton(CoordinatorEntity, ButtonEntity):
    """Representation of a signal switch."""

    def __init__(self, coordinator, proxy, name, path, uid, value):
        """Initialize the switch."""
        super().__init__(coordinator)
        self._name = name
        self.proxy = proxy
        self._path = path
        self._value = value
        self.uid = uid

    @property
    def name(self):
        """Return the name of the switch."""
        return self._name
    
    @property
    def unique_id(self):
        return self.uid

    def press(self) -> None:
        """Press the button."""
        _LOGGER.debug(f"Pressing {self._name}...")
        try:
            self.proxy.set(self._path, self._value)
            _LOGGER.debug(f"Successfully pressed {self._name}")
        except Exception as e:
            _LOGGER.error(f"Error pressing {self._name}: {e}")
            raise