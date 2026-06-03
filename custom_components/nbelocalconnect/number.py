"""NBELocalConnect - Number platform for scan interval and boiler settings."""
import datetime
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from .const import DOMAIN
from logging import getLogger

_LOGGER = getLogger(__name__)


# Settings handled by select, switch or button platform
SKIP_SETTINGS = {
    # Select: L-numbers
    'settings/cleaning/output_ash',
    'settings/cleaning/output_burner',
    'settings/cleaning/output_boiler1',
    'settings/cleaning/output_boiler2',
    'settings/fan/output_exhaust',
    'settings/pump/output',
    'settings/alarm/output',
    'settings/hot_water/output',
    'settings/weather/output_pump',
    'settings/weather2/output_pump',
    'settings/weather/output_down',
    'settings/weather2/output_down',
    'settings/weather/output_up',
    'settings/weather2/output_up',
    'settings/sun/output_pump',
    'settings/sun/output_excess',
    # Select: T-numbers
    'settings/weather/input_reference',
    'settings/weather2/input_reference',
    'settings/weather/input_forward',
    'settings/weather2/input_forward',
    'settings/sun/input_excess',
    'settings/sun/input_dhw',
    'settings/sun/input_collector',
    'settings/sun/input_collector_2',    
    # Select: specific
    'settings/oxygen/regulation',
    'settings/oxygen/lambda_type',
    'settings/hopper/distance_sensor',
    'settings/cleaning/pressure_t7',
    'settings/hot_water/dwh_weather',
    'settings/hot_water/dwh_weather2',
    # Switch: on/off
    'settings/auger/auto_calculation',
    'settings/fan/use_fan_rpm',
    'settings/fan/alarm_fan_rpm',
    'settings/fan/alarm_fan_current',
    'settings/weather/active',
    'settings/weather2/active',
    'settings/oxygen/lambda_expansion_module',
    'settings/misc/expansion_module',
    'settings/sun/excess_from_top',
    # Button
    'settings/ignition/clear_ignitions',
    'settings/auger/forced_run',
    # Read-only sensor (exposed as sensor instead)
    'settings/ignition/ignition_number',
    # Skip
    'settings/misc/dummy',
}


def get_number_config(key):
    """Return (unit, min, max, step) based on key name."""
    key_lower = key.lower()

    # Temperature
    if any(x in key_lower for x in ['temp', 'temperature']):
        return "°C", 0.0, 120.0, 1.0

    # kW power
    if any(x in key_lower for x in ['kw_min', 'kw_max']):
        return "kW", 0.0, 100.0, 0.5

    # Percent / speed / fan
    if any(x in key_lower for x in ['speed', 'pct', 'percent', 'power_min', 'power_max']):
        return "%", 0.0, 100.0, 1.0

    # O2 / oxygen
    if any(x in key_lower for x in ['o2_', 'oxygen']):
        return "%", 0.0, 25.0, 0.1

    # Time (seconds)
    if any(x in key_lower for x in ['_time', 'block_time', 'regulation_time', 'off_delay', 'on_delay']):
        return "s", 0.0, 3600.0, 1.0

    # Pellet weight
    if any(x in key_lower for x in ['pellet', 'min_dose', 'ignition/pellets']):
        return "g", 0.0, 5000.0, 1.0

    # Auger capacity (g)
    if 'auger_capacity' in key_lower or 'auger_consumption' in key_lower:
        return "g", 0.0, 1000.0, 1.0

    # Distance (cm)
    if 'distance' in key_lower:
        return "cm", 0.0, 500.0, 1.0

    # Flow / liters
    if 'liter' in key_lower or 'flow_' in key_lower:
        return "L", 0.0, 100.0, 0.1

    # PID gain / correction values
    if any(x in key_lower for x in ['gain', 'corr_', 'diff', 'addition', 'calibration']):
        return None, -100.0, 100.0, 0.01

    # Period (minutes)
    if 'period' in key_lower:
        return "min", 0.0, 1440.0, 1.0

    # Alarm / min/max thresholds
    if any(x in key_lower for x in ['min_boiler', 'max_shaft', 'ext_stop']):
        return "°C", 0.0, 120.0, 1.0

    # Content / hopper fill
    if 'content' in key_lower:
        return "kg", 0.0, 5000.0, 1.0

    # Fan RPM
    if 'rpm' in key_lower or 'current' in key_lower:
        return None, 0.0, 5000.0, 1.0

    # Exhaust
    if 'exhaust' in key_lower:
        return "%", 0.0, 100.0, 1.0

    # Remain (hours)
    if 'remain' in key_lower:
        return "h", 0.0, 24.0, 0.5

    # Default: dimensionless
    return None, 0.0, 1000.0, 1.0


async def async_setup_entry(hass, config_entry, async_add_entities):
    coordinator = hass.data[DOMAIN][config_entry.entry_id + '_coordinator']
    entry_id = config_entry.entry_id

    entities = []

    # --- Scan Interval ---
    entities.append(
        RTBScanIntervalNumber(coordinator, f'{entry_id}_v2_scan_interval')
    )

    # --- Dynamic settings number entities ---
    all_keys = coordinator.rtbdata.get_all_keys()

    # Skip timer/day data and vacuum
    skip_days = {'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday', 'allweek'}

    for key in all_keys:
        if not key.startswith('settings/'):
            continue

        # Skip timer/day data
        if any(day in key for day in skip_days):
            continue

        # Skip vacuum
        if 'vacuum' in key:
            continue

        # Skip keys handled by select/switch/button platform
        if key in SKIP_SETTINGS:
            continue

        # Numeric values only
        raw = coordinator.rtbdata.get(key)
        try:
            float(raw)
        except (TypeError, ValueError):
            _LOGGER.debug(f"Skipping non-numeric setting: {key} = {raw}")
            continue

        # Name: "Boiler Temp", "Ignition Max Time" etc.
        parts = key.replace('settings/', '').split('/')
        if len(parts) == 2:
            category, item = parts
            name = f"{category.replace('_', ' ').title()} {item.replace('_', ' ').title()}"
        else:
            name = key.replace('settings/', '').replace('_', ' ').replace('/', ' ').title()

        unit, min_val, max_val, step = get_number_config(key)
        uid = f"{entry_id}_v2_num_{key.replace('/', '_')}"

        entities.append(
            RTBSettingsNumber(coordinator, hass, name, key, uid, unit, min_val, max_val, step)
        )
        _LOGGER.debug(f"Created settings number: {name} ({key})")

    _LOGGER.info(f"✓ Total number entities created: {len(entities)}")
    async_add_entities(entities, True)


# =============================================================================
# SCAN INTERVAL
# =============================================================================

class RTBScanIntervalNumber(CoordinatorEntity, NumberEntity, RestoreEntity):
    """Number entity to control the scan interval."""

    def __init__(self, coordinator, uid):
        super().__init__(coordinator)
        self.uid = uid
        self._value = 30.0

    async def async_added_to_hass(self):
        """Restore last value on HA restart."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in ('unknown', 'unavailable'):
            try:
                restored = float(last_state.state)
                self._value = restored
                self.coordinator.update_interval = datetime.timedelta(seconds=int(restored))
                _LOGGER.info(f"Restored scan interval to {restored}s")
            except (ValueError, TypeError):
                pass

    @property
    def name(self):
        return "Scan Interval"

    @property
    def unique_id(self):
        return self.uid

    @property
    def native_value(self):
        return self._value

    @property
    def native_min_value(self):
        return 10.0

    @property
    def native_max_value(self):
        return 300.0

    @property
    def native_step(self):
        return 5.0

    @property
    def native_unit_of_measurement(self):
        return "s"

    @property
    def mode(self):
        return NumberMode.BOX

    async def async_set_native_value(self, value: float) -> None:
        """Update scan interval."""
        self._value = value
        self.coordinator.update_interval = datetime.timedelta(seconds=int(value))
        _LOGGER.info(f"Scan interval changed to {int(value)}s")
        self.async_write_ha_state()

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    @property
    def entity_registry_enabled_default(self):
        return True


# =============================================================================
# SETTINGS NUMBER
# =============================================================================

class RTBSettingsNumber(CoordinatorEntity, NumberEntity):
    """Number entity for a writable boiler setting."""

    def __init__(self, coordinator, hass, name, client_key, uid, unit, min_val, max_val, step):
        super().__init__(coordinator)
        self._hass = hass
        self._name = name
        self.client_key = client_key
        self.uid = uid
        self._unit = unit
        self._min = min_val
        self._max = max_val
        self._step = step

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self.uid

    @property
    def native_value(self):
        raw = self.coordinator.rtbdata.get(self.client_key)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    @property
    def native_min_value(self):
        return self._min

    @property
    def native_max_value(self):
        return self._max

    @property
    def native_step(self):
        return self._step

    @property
    def native_unit_of_measurement(self):
        return self._unit

    @property
    def mode(self):
        return NumberMode.BOX

    @property
    def entity_category(self):
        return EntityCategory.CONFIG

    async def async_set_native_value(self, value: float) -> None:
        """Write new value to boiler via set_setting service."""
        await self._hass.services.async_call(
            DOMAIN,
            "set_setting",
            {"key": self.client_key, "value": value},
        )
        _LOGGER.info(f"Set {self.client_key} = {value}")

    @property
    def extra_state_attributes(self):
        return {
            "datapoint_path": self.client_key,
            "writable": True,
        }

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    @property
    def entity_registry_enabled_default(self):
        key_lower = self.client_key.lower()
        if 'dhw' in key_lower or 'sun' in key_lower:
            return False
        important = [
            'settings/boiler/temp',
            'settings/boiler/diff_over',
            'settings/boiler/diff_under',
            'settings/hopper/content',
            'settings/hopper/min_content',
            'settings/hot_water/temp',
            'settings/auger/kw_max',
            'settings/auger/kw_min',
            'settings/fan/speed_10',
            'settings/fan/speed_50',
            'settings/fan/speed_100',
        ]
        return self.client_key in important