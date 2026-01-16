"""NBELocalConnect - Dynamisk sensor platform."""
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
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
from datetime import datetime
from logging import getLogger

_LOGGER = getLogger(__name__)


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
    
    if any(x in key_lower for x in ['pellet', 'dose', 'trip', 'consumption', 'capacity']) and not 'auger_capacity' in key_lower:
        return "kg", SensorDeviceClass.WEIGHT, SensorStateClass.MEASUREMENT
   
    # Power actual/pct (%) - TILFØJ DENNE!
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
    if 'auger_capacity' in key_lower or 'min_dose' in key_lower:
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
    
    sensors = []
    
    # ========================================================================
    # MANUELLE BINARY SENSORS (specielle)
    # ========================================================================
    sensors.extend([
        RTBBinarySensor(coordinator, 'Boiler Running', 'operating_data/power_pct', 'v2_boiler_running', BinarySensorDeviceClass.HEAT),
        RTBBinarySensor(coordinator, 'Boiler Alarm', 'operating_data/off_on_alarm', 'v2_boiler_alarm', BinarySensorDeviceClass.PROBLEM),
        RTBBinarySensor(coordinator, 'Boiler Pump', 'operating_data/boiler_pump_state', 'v2_boiler_pump', BinarySensorDeviceClass.RUNNING),
        RTBBinarySensor(coordinator, 'DHW Valve', 'operating_data/dhw_valve_state', 'v2_dhw_valve', BinarySensorDeviceClass.OPENING),
        RTBBinarySensor(coordinator, 'House Pump', 'operating_data/house_pump_state', 'v2_house_pump', BinarySensorDeviceClass.RUNNING),
        RTBBinarySensor(coordinator, 'Sun Pump', 'operating_data/sun_pump_state', 'v2_sun_pump', BinarySensorDeviceClass.RUNNING),
    ])
    
    # ========================================================================
    # CONSUMPTION HISTORY SENSORS (specielle - med sorting)
    # ========================================================================
    sensors.extend([
        RTBConsumptionHistorySensor(coordinator, 'Consumption Hourly', 'consumption_data/total_hours', 'v2_consumption_hourly', 24),
        RTBConsumptionHistorySensor(coordinator, 'Consumption Daily', 'consumption_data/total_days', 'v2_consumption_daily', 31),
        RTBConsumptionHistorySensor(coordinator, 'Consumption Monthly', 'consumption_data/total_months', 'v2_consumption_monthly', 12),
        RTBConsumptionHistorySensor(coordinator, 'Consumption Yearly', 'consumption_data/total_years', 'v2_consumption_yearly', 12),
        
        RTBConsumptionHistorySensor(coordinator, 'DHW Consumption Hourly', 'consumption_data/dhw_hours', 'v2_dhw_hourly', 24),
        RTBConsumptionHistorySensor(coordinator, 'DHW Consumption Daily', 'consumption_data/dhw_days', 'v2_dhw_daily', 31),
        RTBConsumptionHistorySensor(coordinator, 'DHW Consumption Monthly', 'consumption_data/dhw_months', 'v2_dhw_monthly', 12),
        RTBConsumptionHistorySensor(coordinator, 'DHW Consumption Yearly', 'consumption_data/dhw_years', 'v2_dhw_yearly', 12),
    ])
    
    # Keys der allerede er lavet sensorer for
    skip_keys = {
        'operating_data/power_pct',
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
    }
    
    # ========================================================================
    # DYNAMISK SCANNING - FIND ALLE KEYS
    # ========================================================================
    _LOGGER.info("Scanning rtbdata for all available keys...")
    
    all_keys = coordinator.rtbdata.get_all_keys()
    _LOGGER.info(f"Found {len(all_keys)} total keys in rtbdata")
    
    # Opret sensor for hver key
    for key in all_keys:
        # Skip allerede håndterede
        if key in skip_keys:
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
        uid = f"v2_{key.replace('/', '_')}"
        
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
        return f"NBE {self.sensorname}"
    
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
        return f"NBE {self.sensorname}"
    
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
        return f"NBE {self.sensorname}"
    
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
        return SensorStateClass.MEASUREMENT
        
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
