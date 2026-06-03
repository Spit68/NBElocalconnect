"""NBELocalConnect - Dynamisk sensor platform."""
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from .const import DOMAIN
from datetime import datetime, timedelta
from logging import getLogger

_LOGGER = getLogger(__name__)

def _normalize_translation_key(value):
    """Normalize NBE numeric message/state values for translation lookup."""
    if value is None:
        return None
    key = str(value).strip()
    # Some values may arrive as "5.0"; translation JSON normally uses "5".
    try:
        number = float(key)
        if number.is_integer():
            key = str(int(number))
    except (ValueError, TypeError):
        pass
    return key


def _translate_boiler_value(translations, section, value):
    """Translate a boiler value using boiler_messages JSON, fallback to raw value."""
    key = _normalize_translation_key(value)
    if key is None:
        return None

    section_data = translations.get(section, {})
    if not isinstance(section_data, dict):
        return key

    return section_data.get(key, key)



def get_sensor_config(key):
    """Returner (unit, device_class, state_class) baseret på key navn."""
    key_lower = key.lower()
    
    # Temperature
    if any(x in key_lower for x in ['temp', 'temperature']):
        return "°C", SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT
    
    # Weight (kg)
    if 'content' in key_lower and 'min_content' not in key_lower:
        # Special: content skal ganges med 10
        return "kg", SensorDeviceClass.WEIGHT, SensorStateClass.MEASUREMENT
    
    if any(x in key_lower for x in ['pellet', 'dose', 'trip', 'consumption', 'capacity']) and not 'auger_capacity' in key_lower and not 'auger_consumption' in key_lower:
        return "kg", SensorDeviceClass.WEIGHT, SensorStateClass.MEASUREMENT
   
    # Power actual/pct (%)
    if '_power_actual' in key_lower or '_power_pct' in key_lower:
        return "%", None, SensorStateClass.MEASUREMENT
    
    # Power (kW)
    if 'kw' in key_lower or '_power' in key_lower:
        return "kW", SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT    

    # Wind
    if 'wind_speed' in key_lower:
        return "m/s", SensorDeviceClass.WIND_SPEED, SensorStateClass.MEASUREMENT

    if 'wind_direction' in key_lower:
        return "°", None, SensorStateClass.MEASUREMENT        
  
    # Percent (%)
    if any(x in key_lower for x in ['pct', 'percent', '_speed', 'level', 'oxygen', 'o2_', 'clean', 'uptime', 'humid']):
        return "%", None, SensorStateClass.MEASUREMENT
    
    # Pressure
    if 'pressure' in key_lower:
        return "hPa", SensorDeviceClass.ATMOSPHERIC_PRESSURE, SensorStateClass.MEASUREMENT
   
    # Duration (sekunder)
    if any(x in key_lower for x in ['auger_run', 'auger_pause', '_time']):
        return "s", SensorDeviceClass.DURATION, None
    
    # Gram
    if 'auger_capacity' in key_lower or 'min_dose' in key_lower or 'settings/hopper/auger_consumption' in key:
        return "g", None, SensorStateClass.MEASUREMENT
    
    # Distance (cm)
    if 'distance' in key_lower:
        return "cm", SensorDeviceClass.DISTANCE, SensorStateClass.MEASUREMENT
    
    # Flow/Liters
    if 'liter' in key_lower or 'flow_' in key_lower:
        return "L", None, SensorStateClass.MEASUREMENT
    
    # Current (mA)
    if 'ampere' in key_lower:
        return "mA", SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT
    
    # Frequency (Hz)
    if 'freq' in key_lower:
        return "Hz", SensorDeviceClass.FREQUENCY, SensorStateClass.MEASUREMENT
    
    # Gain/PID values (decimal tal uden unit)
    if 'gain' in key_lower or 'diff' in key_lower or 'part_' in key_lower or 'corr_' in key_lower:
        return None, None, SensorStateClass.MEASUREMENT
    
    # Default: ingen unit, bare værdi
    return None, None, None


async def async_setup_entry(hass, entry, async_add_entities):
    """Setup sensors dynamisk."""
    _LOGGER.info("Setting up NBE Local Connect sensors with DYNAMIC scanning...")
    
    coordinator = hass.data[DOMAIN][entry.entry_id+'_coordinator']
    
    # Tilføj entry_id til unique_id
    entry_id = entry.entry_id
    
    sensors = []
    
    # BINARY SENSORS
    sensors.extend([
        RTBBinarySensor(coordinator, 'Boiler Running', 'operating_data/power_pct', f'{entry_id}_v2_boiler_running', BinarySensorDeviceClass.HEAT),
        RTBBinarySensor(coordinator, 'Boiler Alarm', 'operating_data/off_on_alarm', f'{entry_id}_v2_boiler_alarm', BinarySensorDeviceClass.PROBLEM),
        RTBBinarySensor(coordinator, 'Boiler Pump', 'operating_data/boiler_pump_state', f'{entry_id}_v2_boiler_pump', BinarySensorDeviceClass.RUNNING),
        RTBBinarySensor(coordinator, 'DHW Valve', 'operating_data/dhw_valve_state', f'{entry_id}_v2_dhw_valve', BinarySensorDeviceClass.OPENING),
        RTBBinarySensor(coordinator, 'House Pump', 'operating_data/house_pump_state', f'{entry_id}_v2_house_pump', BinarySensorDeviceClass.RUNNING),
        RTBBinarySensor(coordinator, 'Sun Pump', 'operating_data/sun_pump_state', f'{entry_id}_v2_sun_pump', BinarySensorDeviceClass.RUNNING),
    ])
    
    # CONSUMPTION HISTORY SENSORS
    sensors.extend([
        RTBConsumptionHistorySensor(coordinator, 'Consumption Hourly', 'consumption_data/total_hours', f'{entry_id}_v2_consumption_hourly', 24),
        RTBDailyConsumptionDBSensor(coordinator, 'Consumption Daily', 'pellets', f'{entry_id}_v2_consumption_daily'),
        RTBConsumptionHistorySensor(coordinator, 'Consumption Monthly', 'consumption_data/total_months', f'{entry_id}_v2_consumption_monthly', 12),
        RTBStokerCloudYearlySensor(coordinator, 'Consumption Yearly', 'pellets', f'{entry_id}_v2_consumption_yearly'),

        RTBConsumptionHistorySensor(coordinator, 'DHW Consumption Hourly', 'consumption_data/dhw_hours', f'{entry_id}_v2_dhw_hourly', 24),
        RTBDailyConsumptionDBSensor(coordinator, 'DHW Consumption Daily', 'dhw', f'{entry_id}_v2_dhw_daily'),
        RTBConsumptionHistorySensor(coordinator, 'DHW Consumption Monthly', 'consumption_data/dhw_months', f'{entry_id}_v2_dhw_monthly', 12),
        RTBStokerCloudYearlySensor(coordinator, 'DHW Consumption Yearly', 'dhw', f'{entry_id}_v2_dhw_yearly'),
    ])
    
    # Keys der allerede er lavet sensorer for
    skip_keys = {
        'operating_data/off_on_alarm',
        'operating_data/boiler_pump_state',
        'operating_data/dhw_valve_state',
        'operating_data/house_pump_state',
        'operating_data/sun_pump_state',
        'consumption_data/total_hours',
        'consumption_data/total_days',
        'consumption_data/total_months',
        'consumption_data/total_years',
        'consumption_data/dhw_hours',
        'consumption_data/dhw_days',
        'consumption_data/dhw_months',
        'consumption_data/dhw_years',
        'operating_data/NA',
    }
    
    # Blacklist: Skip these from advanced_data (duplicates of operating_data)
    advanced_data_blacklist = {
        'advanced_data/boiler_power_kw',      # Duplikerer operating_data/power_kw
        'advanced_data/boiler_power_actual',  # Duplikerer operating_data/power_pct
    }
    
    # ========================================================================
    # DYNAMIC SCANNING - FIND ALL KEYS
    # ========================================================================
    _LOGGER.info("Scanning rtbdata for all available keys...")
    
    all_keys = coordinator.rtbdata.get_all_keys()
    _LOGGER.info(f"Found {len(all_keys)} total keys in rtbdata")
    
    # Create sensor for each key
    for key in all_keys:
        # Skip already handled keys
        if key in skip_keys:
            continue
        
        # Skip blacklisted advanced_data duplicates
        if key in advanced_data_blacklist:
            _LOGGER.debug(f"Skipping advanced_data duplicate: {key}")
            continue
        
        # Skip settings (håndteres af number platform)
        # Exception: settings that are read-only sensors
        SETTINGS_AS_SENSORS = {'settings/ignition/ignition_number'}
        if key.startswith('settings/') and key not in SETTINGS_AS_SENSORS:
            continue

        # Skip binære timer data
        if any(day in key for day in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday', 'allweek']):
            _LOGGER.debug(f"Skipping binary timer data: {key}")
            continue
        
        # Skip counter
        if key == 'consumption_data/counter':
            continue
        
        # Skip vacuum
        if 'vacuum' in key:
            continue
        
        # Lav sensor navn
        # Fjern endpoint prefix og formatér pænt
        if key.startswith('operating_data/'):
            name = key.replace('operating_data/', '').replace('_', ' ').title()
        elif key.startswith('advanced_data/'):
            name = key.replace('advanced_data/', '').replace('_', ' ').title()
        elif key.startswith('settings/'):
            # For settings: behold kategori i navn
            parts = key.replace('settings/', '').split('/')
            if len(parts) == 2:
                category, item = parts
                name = f"{category.title()} {item.replace('_', ' ').title()}"
            else:
                name = key.replace('settings/', '').replace('_', ' ').replace('/', ' ').title()
        else:
            name = key.replace('/', ' ').replace('_', ' ').title()
        
        # Få sensor config
        unit, device_class, state_class = get_sensor_config(key)
        
        # Lav unique ID med prefix for at undgå kollision med gamle sensorer
        uid = f"{coordinator.entry_id}_v2_{key.replace('/', '_')}"
        
        # Opret sensor
        sensor = RTBDynamicSensor(
            coordinator,
            name,
            key,
            uid,
            unit,
            device_class,
            state_class
        )
        
        sensors.append(sensor)
        _LOGGER.debug(f"Created: {name} ({key}) - {unit}")
    
    _LOGGER.info(f"✓ Total sensors created: {len(sensors)}")
    
    # INFO MESSAGE SENSOR (tal)
    sensors.append(
        RTBInfoSensor(coordinator, f'{entry_id}_v2_info_message')
    )

    # ALARM MSG SENSOR (tekst fra boiler_state oversættelse)
    sensors.append(
        RTBAlarmMsgSensor(coordinator, f'{entry_id}_v2_alarm_msg')
    )

    # SUBSTATE MSG SENSOR (tekst fra boiler_substate oversættelse)
    sensors.append(
        RTBSubstateMsgSensor(coordinator, f'{entry_id}_v2_substate_msg')
    )

    # INFO MSG SENSOR (tekst fra boiler_info oversættelse)
    sensors.append(
        RTBInfoMsgSensor(coordinator, f'{entry_id}_v2_info_msg')
    )

    # STATE COUNTDOWN SENSOR
    sensors.append(
        RTBCountdownSensor(coordinator, f'{entry_id}_v2_state_countdown')
    )

    # AUGER WEIGHING TEST COUNTDOWN
    sensors.append(
        RTBAugerCountdownSensor(coordinator, f'{entry_id}_v2_auger_countdown')
    )

    # ENERGY SENSORS
    energy_kwh = RTBEnergySensor(coordinator, f'{entry_id}_v2_energy_kwh', "kWh")
    energy_wh = RTBEnergySensor(coordinator, f'{entry_id}_v2_energy_wh', "Wh")
    sensors.append(energy_kwh)
    sensors.append(energy_wh)
    # Store references so reset button can access them
    hass.data[DOMAIN][entry_id + '_energy_kwh'] = energy_kwh
    hass.data[DOMAIN][entry_id + '_energy_wh'] = energy_wh

    async_add_entities(sensors, True)


# =============================================================================
# SENSOR CLASSES
# =============================================================================

class RTBDynamicSensor(CoordinatorEntity, SensorEntity):
    """Dynamisk sensor for enhver key."""
    
    def __init__(self, coordinator, name, client_key, uid, unit, device_class, state_class):
        """Initialize."""
        super().__init__(coordinator)
        self.client_key = client_key
        self.sensorname = name
        self.uid = uid
        self._unit = unit
        self._device_class = device_class
        self._state_class = state_class
    
    @property
    def name(self):
        return self.sensorname
    
    @property
    def unique_id(self):
        return self.uid
    
    @property
    def state(self):
        """Return state."""
        data = self.coordinator.rtbdata.get(self.client_key)

        # content skal ganges med 10 (kg)
        if 'content' in self.client_key and 'min_content' not in self.client_key and 'operating_data' in self.client_key:
            try:
                return float(data) * 10 if data else None
            except:
                return data
        
        return data
    
    @property
    def unit_of_measurement(self):
        return self._unit
    
    @property
    def device_class(self):
        return self._device_class
    
    @property
    def state_class(self):
        return self._state_class
        
    @property
    def extra_state_attributes(self):
        """Return extra attributes including datapoint path."""
        is_writable = self.client_key.startswith("settings/")
        return {
            "datapoint_path": self.client_key,
            "writable": is_writable,
        }
        
    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.entry_id)},
        }        

    @property
    def entity_category(self):
        """Return entity category."""
        # operating_data/time og sw_version keys → DIAGNOSTIC
        if self.client_key == 'operating_data/time':
            return EntityCategory.DIAGNOSTIC
        if 'sw_version' in self.client_key.lower() or 'version' in self.client_key.lower():
            return EntityCategory.DIAGNOSTIC

        return None

    @property
    def entity_registry_enabled_default(self):
        """Return if entity should be enabled by default."""
        if 'dhw' in self.client_key.lower():
            return False
        
        if 'sun' in self.client_key.lower():
            return False
    
        if self.client_key.endswith('/NA') or '/na' in self.client_key.lower():
            return False

        disabled_by_default = [
            'operating_data/air_flow',
            'operating_data/flow1',
            'operating_data/flow2',
            'operating_data/flow3',
            'operating_data/flow4',
            'operating_data/forward_ref',
            'operating_data/output_ext',
            'operating_data/output_wireless',
            'operating_data/contact2',
            'operating_data/dl_progress',
            'operating_data/pressure',
            'operating_data/corr_low',
            'operating_data/corr_medium',
            'operating_data/corr_high',
            'operating_data/distance',
            'operating_data/feed_low',
            'operating_data/feed_medium',
            'operating_data/feed_high',
            'operating_data/t7_temp',
        ]
        if self.client_key in disabled_by_default:
            return False

        if self.client_key.startswith('operating_data/'):
            return True
    
        if self.client_key.startswith('consumption_data/'):
            return True

        important_settings = [
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
        if self.client_key in important_settings:
            return True

        # Settings exposed as read-only sensors are always enabled
        if self.client_key in {'settings/ignition/ignition_number'}:
            return True
    
        return False


class RTBAugerCountdownSensor(CoordinatorEntity, SensorEntity):
    """Real-time countdown sensor for the auger weighing test (settings/auger/forced_run)."""

    def __init__(self, coordinator, uid):
        super().__init__(coordinator)
        self.uid = uid
        self._last_value = 0
        self._last_update = None
        self._unsub_timer = None

    async def async_added_to_hass(self):
        """Start 1-second timer when added to HA."""
        await super().async_added_to_hass()
        self._unsub_timer = async_track_time_interval(
            self.hass,
            self._tick,
            timedelta(seconds=1)
        )

    async def async_will_remove_from_hass(self):
        """Cancel timer when removed."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None

    @callback
    def _tick(self, now=None):
        """Called every second to update countdown display."""
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Sync countdown to boiler value on each poll."""
        raw = self.coordinator.rtbdata.get('settings/auger/forced_run')
        try:
            val = int(float(raw)) if raw else 0
        except (ValueError, TypeError):
            val = 0

        if val > 0:
            self._last_value = val
            self._last_update = datetime.now()
        else:
            self._last_value = 0
            self._last_update = None

        self.async_write_ha_state()

    @property
    def name(self):
        return "Auger Weighing Test Timer"

    @property
    def unique_id(self):
        return self.uid

    @property
    def state(self):
        """Return current countdown value in seconds."""
        if not self._last_update or self._last_value <= 0:
            return 0
        elapsed = (datetime.now() - self._last_update).total_seconds()
        remaining = max(0, self._last_value - int(elapsed))
        return remaining

    @property
    def unit_of_measurement(self):
        return "s"

    @property
    def entity_registry_enabled_default(self):
        return True

    @property
    def extra_state_attributes(self):
        remaining = self.state
        if remaining > 0:
            minutes = remaining // 60
            seconds = remaining % 60
            formatted = f"{minutes}:{seconds:02d}"
        else:
            formatted = "0:00"
        return {
            "formatted": formatted,
            "datapoint_path": "settings/auger/forced_run",
            "writable": False,
        }

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

class RTBBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor."""
    
    def __init__(self, coordinator, name, client_key, uid, device_class):
        super().__init__(coordinator)
        self.client_key = client_key
        self.sensorname = name
        self.uid = uid
        self._device_class = device_class
    
    @property
    def name(self):
        return self.sensorname
    
    @property
    def unique_id(self):
        return self.uid
    
    @property
    def is_on(self):
        s = self.coordinator.rtbdata.get(self.client_key)
        
        if "power_pct" in self.client_key:
            try:
                return int(s) > 0
            except:
                return False
        
        if "off_on_alarm" in self.client_key:
            return s == "2"
        
        if "state" in self.client_key:
            return s == "1"
        
        return False
    
    @property
    def device_class(self):
        return self._device_class
        
    @property
    def extra_state_attributes(self):
        """Return extra attributes including datapoint path."""
        return {
            "datapoint_path": self.client_key,
            "writable": False,
        }

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.entry_id)},
        }     

    @property
    def entity_category(self):
        return None    

    @property
    def entity_registry_enabled_default(self):
        if 'dhw' in self.client_key.lower() or 'sun' in self.client_key.lower():
            return False
        return True     

class RTBConsumptionHistorySensor(CoordinatorEntity, SensorEntity):
    """Consumption history sensor med sorting."""
    
    def __init__(self, coordinator, name, client_key, uid, expected_count):
        super().__init__(coordinator)
        self.client_key = client_key
        self.sensorname = name
        self.uid = uid
        self.expected_count = expected_count
    
    @property
    def name(self):
        return self.sensorname
    
    @property
    def unique_id(self):
        return self.uid
    
    @property
    def state(self):
        """Return current period (nyeste)."""
        data = self.coordinator.rtbdata.get(self.client_key)
        if data:
            values = self._parse_consumption_data(data)
            if values and len(values) > 0:
                return values[0]
        return None
    
    @property
    def unit_of_measurement(self):
        return "kg"
    
    @property
    def device_class(self):
        return SensorDeviceClass.WEIGHT
    
    @property
    def state_class(self):
        return None
        
    @property
    def extra_state_attributes(self):
        """Return attributes med values array."""
        data = self.coordinator.rtbdata.get(self.client_key)
        if not data:
            return {}
        
        values = self._parse_consumption_data(data)
        if not values:
            return {}
        
        return {
            "datapoint_path": self.client_key,
            "writable": False,
            "values": values,
            "count": len(values),
            "total": round(sum(values), 2),
            "average": round(sum(values) / len(values), 2) if values else 0,
            "max": round(max(values), 2) if values else 0,
            "min": round(min(values), 2) if values else 0,
        }
        
    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.entry_id)},
        }        
    
    def _parse_consumption_data(self, data_string):
        """Parse og sorter consumption data."""
        try:
            values_str = data_string.split('=')[1] if '=' in data_string else data_string
            values = [float(v.strip()) for v in values_str.split(',') if v.strip()]
            
            # HOURLY: Bagud fra current hour
            if 'hours' in self.client_key or 'hourly' in self.client_key:
                current_hour = datetime.now().hour
                result = []
                for i in range(24):
                    index = (current_hour - i) % 24
                    result.append(values[index])
                return result
            
            # DAILY: Reverse per måned
            elif 'days' in self.client_key or 'daily' in self.client_key:
                values = values[:31]
                current_day = datetime.now().day
                this_month = values[:current_day]
                last_month = values[current_day:]
                this_month.reverse()
                last_month.reverse()
                return this_month + last_month
            
            # MONTHLY: Bagud fra current month
            elif 'months' in self.client_key or 'monthly' in self.client_key:
                current_month = datetime.now().month
                result = []
                for i in range(12):
                    index = (current_month - 1 - i) % 12
                    result.append(values[index])
                return result
            
            # YEARLY: Raw (afventer firmware fix)
            else:
                return values
        
        except (ValueError, IndexError) as e:
            _LOGGER.error(f"Error parsing {self.client_key}: {e}")
            return []

    @property
    def entity_category(self):
        return None    

    @property
    def entity_registry_enabled_default(self):
        if 'dhw' in self.client_key.lower():
            return False
        return True


class RTBInfoSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = False
    """Sensor showing current info message number from NBE boiler."""

    def __init__(self, coordinator, uid):
        """Initialize."""
        super().__init__(coordinator)
        self.uid = uid

    @property
    def name(self):
        return "Info Message"

    @property
    def unique_id(self):
        return self.uid

    @property
    def state(self):
        """Return info message numbers."""
        msgs = getattr(self.coordinator, 'info_messages', [])
        if not msgs:
            return 0
        return ",".join(str(n) for n in msgs)

    @property
    def extra_state_attributes(self):
        return {
            "datapoint_path": "info/",
            "writable": False,
        }

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.coordinator.entry_id)},
        }

    @property
    def entity_category(self):
        return None

    @property
    def entity_registry_enabled_default(self):
        return True


class RTBAlarmMsgSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = False
    """Sensor showing translated boiler state text."""

    def __init__(self, coordinator, uid):
        super().__init__(coordinator)
        self.uid = uid

    @property
    def name(self):
        return "Alarm Message"

    @property
    def unique_id(self):
        return self.uid

    @property
    def state(self):
        """Return translated boiler state text."""
        data = self.coordinator.rtbdata.get('operating_data/state')
        if data is None:
            return None
        return _translate_boiler_value(
            self.coordinator.translations,
            "boiler_state",
            data,
        )

    @property
    def extra_state_attributes(self):
        raw_state = self.coordinator.rtbdata.get('operating_data/state')
        translations = self.coordinator.translations.get("boiler_state", {})
        return {
            "state_number": raw_state,
            "translation_key": _normalize_translation_key(raw_state),
            "translation_count": len(translations) if isinstance(translations, dict) else 0,
            "datapoint_path": "operating_data/state",
            "writable": False,
            "alarm_history": self.coordinator.get_translated_alarm_history(),
        }

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    @property
    def entity_category(self):
        return None

    @property
    def entity_registry_enabled_default(self):
        return True


class RTBSubstateMsgSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing translated substate text."""

    def __init__(self, coordinator, uid):
        super().__init__(coordinator)
        self.uid = uid

    @property
    def name(self):
        return "Substate Message"

    @property
    def unique_id(self):
        return self.uid

    @property
    def state(self):
        """Return translated substate text."""
        substate = self.coordinator.rtbdata.get('operating_data/substate')
        key = _normalize_translation_key(substate)
        if not key or key == '0':
            return ""
        return _translate_boiler_value(
            self.coordinator.translations,
            "boiler_substate",
            substate,
        )

    @property
    def extra_state_attributes(self):
        raw_substate = self.coordinator.rtbdata.get('operating_data/substate')
        translations = self.coordinator.translations.get("boiler_substate", {})
        return {
            "substate_number": raw_substate,
            "translation_key": _normalize_translation_key(raw_substate),
            "translation_count": len(translations) if isinstance(translations, dict) else 0,
            "datapoint_path": "operating_data/substate",
            "writable": False,
        }

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    @property
    def entity_category(self):
        return None

    @property
    def entity_registry_enabled_default(self):
        return True


class RTBInfoMsgSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing translated info message text."""

    def __init__(self, coordinator, uid):
        super().__init__(coordinator)
        self.uid = uid

    @property
    def name(self):
        return "Info Message Text"

    @property
    def unique_id(self):
        return self.uid

    @property
    def state(self):
        """Return translated info message texts separated by |."""
        msgs = getattr(self.coordinator, 'info_messages', [])
        if not msgs:
            return ""
        parts = []
        for num in msgs:
            text = _translate_boiler_value(
                self.coordinator.translations,
                "boiler_info",
                num,
            )
            if text:
                parts.append(text)
        return " | ".join(parts) if parts else ""

    @property
    def extra_state_attributes(self):
        translations = self.coordinator.translations.get("boiler_info", {})
        return {
            "info_numbers": getattr(self.coordinator, 'info_messages', []),
            "translation_keys": [
                _normalize_translation_key(num)
                for num in getattr(self.coordinator, 'info_messages', [])
            ],
            "translation_count": len(translations) if isinstance(translations, dict) else 0,
            "datapoint_path": "info/",
            "writable": False,
        }

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    @property
    def entity_category(self):
        return None

    @property
    def entity_registry_enabled_default(self):
        return True

class RTBCountdownSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing a real-time countdown based on substate_sec from the boiler."""

    # States where countdown should be zero (boiler not in an active timed step)
    IDLE_STATES = {'5', '6', '9', '10', '22', '23', '24', '25'}

    def __init__(self, coordinator, uid):
        super().__init__(coordinator)
        self.uid = uid
        self._last_value = 0
        self._last_update = None
        self._unsub_timer = None

    async def async_added_to_hass(self):
        """Start 1-second timer when added to HA."""
        await super().async_added_to_hass()
        self._unsub_timer = async_track_time_interval(
            self.hass,
            self._tick,
            timedelta(seconds=1)
        )

    async def async_will_remove_from_hass(self):
        """Cancel timer when removed."""
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None

    @callback
    def _tick(self, now=None):
        """Called every second to update countdown display."""
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from coordinator — sync countdown to boiler value."""
        # Check if boiler is in an idle state — if so, reset to 0
        boiler_state = self.coordinator.rtbdata.get('operating_data/state')
        if str(boiler_state) in self.IDLE_STATES:
            self._last_value = 0
            self._last_update = None
            self.async_write_ha_state()
            return

        raw = self.coordinator.rtbdata.get('operating_data/substate_sec')
        try:
            val = int(raw) if raw else 0
        except (ValueError, TypeError):
            val = 0

        if val > 1:
            self._last_value = val
            self._last_update = datetime.now()
        else:
            self._last_value = 0
            self._last_update = None

        self.async_write_ha_state()

    @property
    def name(self):
        return "State Countdown"

    @property
    def unique_id(self):
        return self.uid

    @property
    def state(self):
        """Return current countdown value in seconds."""
        if not self._last_update or self._last_value <= 0:
            return 0
        elapsed = (datetime.now() - self._last_update).total_seconds()
        remaining = max(0, self._last_value - int(elapsed))
        return remaining

    @property
    def unit_of_measurement(self):
        return "s"

    @property
    def extra_state_attributes(self):
        remaining = self.state
        if remaining > 0:
            minutes = remaining // 60
            seconds = remaining % 60
            formatted = f"{minutes}:{seconds:02d}"
        else:
            formatted = "0:00"
        return {
            "formatted": formatted,
            "datapoint_path": "operating_data/substate_sec",
            "writable": False,
        }

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    @property
    def entity_category(self):
        return None

    @property
    def entity_registry_enabled_default(self):
        return True

class RTBEnergySensor(CoordinatorEntity, SensorEntity, RestoreEntity):
    """Accumulated energy sensor calculated from operating_data/power_kw.

    Integrates power (kW) over time using a left Riemann sum with the
    coordinator scan interval as the time step. Persists across HA restarts
    via RestoreEntity. Unit is either 'kWh' or 'Wh'.
    """

    def __init__(self, coordinator, uid, unit):
        super().__init__(coordinator)
        self.uid = uid
        self._unit = unit  # "kWh" or "Wh"
        self._accumulated_kwh = 0.0
        self._last_update_time = None

    async def async_added_to_hass(self):
        """Restore accumulated value from last known state on HA startup."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state and last_state.state not in (None, "unknown", "unavailable"):
            try:
                restored = float(last_state.state)
                if self._unit == "kWh":
                    self._accumulated_kwh = restored
                else:  # Wh
                    self._accumulated_kwh = restored / 1000.0
            except (ValueError, TypeError):
                pass
        # Set baseline time — first coordinator update will use this as t0
        self._last_update_time = datetime.now()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Accumulate energy delta on each coordinator poll."""
        now = datetime.now()

        if self._last_update_time is not None:
            delta_hours = (now - self._last_update_time).total_seconds() / 3600
            raw = self.coordinator.rtbdata.get("operating_data/power_kw")
            try:
                power_kw = float(raw) if raw else 0.0
            except (ValueError, TypeError):
                power_kw = 0.0

            delta_kwh = power_kw * delta_hours
            if delta_kwh > 0:
                self._accumulated_kwh += delta_kwh

        self._last_update_time = now
        self.async_write_ha_state()

    @property
    def name(self):
        if self._unit == "kWh":
            return "Energy kWh"
        return "Energy Wh"

    @property
    def unique_id(self):
        return self.uid

    @property
    def state(self):
        if self._unit == "kWh":
            return round(self._accumulated_kwh, 3)
        return round(self._accumulated_kwh * 1000, 0)

    @property
    def unit_of_measurement(self):
        return self._unit

    @property
    def device_class(self):
        return SensorDeviceClass.ENERGY

    @property
    def state_class(self):
        return SensorStateClass.TOTAL_INCREASING

    @property
    def extra_state_attributes(self):
        return {
            "datapoint_path": "operating_data/power_kw",
            "writable": False,
        }

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    @property
    def entity_category(self):
        return None

    @property
    def entity_registry_enabled_default(self):
        return True

class RTBStokerCloudYearlySensor(CoordinatorEntity, SensorEntity):
    """Yearly consumption sensor populated from StokerCloud data."""

    def __init__(self, coordinator, name, data_key, uid):
        super().__init__(coordinator)
        self.sensorname = name
        self.data_key = data_key  # 'pellets' or 'dhw'
        self.uid = uid

    @property
    def name(self):
        return self.sensorname

    @property
    def unique_id(self):
        return self.uid

    def _get_values(self):
        if self.data_key == 'pellets':
            return getattr(self.coordinator, 'stokercloud_pellets', [])
        return getattr(self.coordinator, 'stokercloud_dhw', [])

    @property
    def state(self):
        values = self._get_values()
        return round(values[0], 3) if values else None

    @property
    def unit_of_measurement(self):
        return "kg"

    @property
    def device_class(self):
        return SensorDeviceClass.WEIGHT

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def extra_state_attributes(self):
        values = self._get_values()
        if not values:
            return {"values": [], "count": 0}
        return {
            "values": [round(v, 3) for v in values],
            "count": len(values),
            "total": round(sum(values), 2),
            "average": round(sum(values) / len(values), 2),
            "max": round(max(values), 2),
            "min": round(min(values), 2),
        }

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    @property
    def entity_category(self):
        return None

    @property
    def entity_registry_enabled_default(self):
        if self.data_key == 'dhw':
            return False
        return True

class RTBDailyConsumptionDBSensor(CoordinatorEntity, SensorEntity):
    """Daily consumption sensor der læser fra HA DB via koordinator hukommelse."""

    def __init__(self, coordinator, name, data_key, uid):
        super().__init__(coordinator)
        self.sensorname = name
        self.data_key = data_key  # 'pellets' or 'dhw'
        self.uid = uid

    @property
    def name(self):
        return self.sensorname

    @property
    def unique_id(self):
        return self.uid

    def _get_values(self):
        if self.data_key == 'pellets':
            return getattr(self.coordinator, 'stokercloud_daily_pellets', [])
        return getattr(self.coordinator, 'stokercloud_daily_dhw', [])

    @property
    def state(self):
        values = self._get_values()
        return round(values[0], 3) if values else None

    @property
    def unit_of_measurement(self):
        return "kg"

    @property
    def device_class(self):
        return SensorDeviceClass.WEIGHT

    @property
    def state_class(self):
        return SensorStateClass.MEASUREMENT

    @property
    def extra_state_attributes(self):
        values = self._get_values()
        if not values:
            return {"values": [], "count": 0}
        return {
            "values": [round(v, 3) for v in values],
            "count": len(values),
            "total": round(sum(values), 2),
            "average": round(sum(values) / len(values), 2),
            "max": round(max(values), 2),
            "min": round(min(values), 2),
        }

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.coordinator.entry_id)}}

    @property
    def entity_category(self):
        return None

    @property
    def entity_registry_enabled_default(self):
        if self.data_key == 'dhw':
            return False
        return True