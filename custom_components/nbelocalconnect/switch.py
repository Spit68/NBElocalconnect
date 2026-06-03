"""NBELocalConnect - Switch platform for boolean boiler settings."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN
from logging import getLogger

_LOGGER = getLogger(__name__)

# Boolean settings: (key, name, enabled_default)
SWITCH_SETTINGS = [
    ('settings/fan/use_fan_rpm',               'Fan Use Fan RPM',               False),
    ('settings/fan/alarm_fan_rpm',             'Fan Alarm Fan RPM',             False),
    ('settings/fan/alarm_fan_current',         'Fan Alarm Fan Current',         False),
    ('settings/auger/auto_calculation',        'Auger Auto Calculation',        False),
    ('settings/weather/active',                'Weather Active',                False),
    ('settings/weather2/active',               'Weather 2 Active',              False),
    ('settings/oxygen/lambda_expansion_module','Oxygen Lambda Expansion Module',False),
    ('settings/misc/expansion_module',         'Misc Expansion Module',         False),
    ('settings/sun/excess_from_top',           'Sun Excess From Top',           False),
]


async def async_setup_entry(hass, config_entry, async_add_entities):
    coordinator = hass.data[DOMAIN][config_entry.entry_id + '_coordinator']
    entry_id = config_entry.entry_id

    entities = []
    all_keys = set(coordinator.rtbdata.get_all_keys())

    for key, name, enabled in SWITCH_SETTINGS:
        if key not in all_keys:
            _LOGGER.debug(f"Switch key not found on this boiler: {key}")
            continue
        uid = f"{entry_id}_v2_sw_{key.replace('/', '_')}"
        entities.append(NBESettingsSwitch(coordinator, hass, name, key, uid, enabled))

    _LOGGER.info(f"✓ Switch entities created: {len(entities)}")
    async_add_entities(entities)


class NBESettingsSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity for a boolean boiler setting (0/1)."""

    def __init__(self, coordinator, hass, name, client_key, uid, enabled_default):
        super().__init__(coordinator)
        self._hass = hass
        self._name = name
        self.client_key = client_key
        self.uid = uid
        self._enabled_default = enabled_default
        self._pending_state = None  # Snap-back fix

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self.uid

    @property
    def is_on(self) -> bool:
        if self._pending_state is not None:
            return self._pending_state
        raw = self.coordinator.rtbdata.get(self.client_key)
        try:
            return int(float(raw)) != 0
        except (TypeError, ValueError):
            return False

    def _handle_coordinator_update(self) -> None:
        """Clear pending state when coordinator updates."""
        self._pending_state = None
        super()._handle_coordinator_update()

    @property
    def entity_category(self):
        return EntityCategory.CONFIG

    @property
    def extra_state_attributes(self):
        return {
            "datapoint_path": self.client_key,
            "writable": True,
        }

    async def async_turn_on(self, **kwargs) -> None:
        self._pending_state = True
        self.async_write_ha_state()
        await self._hass.services.async_call(
            DOMAIN, "set_setting",
            {"key": self.client_key, "value": 1},
        )
        _LOGGER.info(f"Switch ON: {self.client_key}")

    async def async_turn_off(self, **kwargs) -> None:
        self._pending_state = False
        self.async_write_ha_state()
        await self._hass.services.async_call(
            DOMAIN, "set_setting",
            {"key": self.client_key, "value": 0},
        )
        _LOGGER.info(f"Switch OFF: {self.client_key}")

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    @property
    def entity_registry_enabled_default(self):
        return self._enabled_default