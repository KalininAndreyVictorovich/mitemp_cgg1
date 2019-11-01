""""
Read data from Xiaomi Clear Grass Thermometer Hygrometer sensor with E-Ink.
"""
from datetime import datetime, timedelta
import logging
from threading import Lock
from bluepy import btle
import typing

MI_TEMPERATURE = "temperature"
MI_HUMIDITY = "humidity"
MI_BATTERY = "battery"

_UUID_SERVICE_GENERIC_ACCESS = '00001800-0000-1000-8000-00805F9B34FB'
_UUID_CHAR_NAME = '00002A00-0000-1000-8000-00805F9B34FB'

_UUID_SERVICE_DEVICE_INFO = '0000180A-0000-1000-8000-00805F9B34FB'
_UUID_CHAR_MANUFACTURER = '00002A29-0000-1000-8000-00805F9B34FB'
_UUID_CHAR_MODEL = '00002A24-0000-1000-8000-00805F9B34FB'
_UUID_CHAR_FIRMWARE = '00002A26-0000-1000-8000-00805F9B34FB'

_UUID_SERVICE_DATA = '22210000-554A-4546-5542-46534450464D'
_UUID_CHAR_DATA = '00000100-0000-1000-8000-00805F9B34FB'
_UUID_DESC_CCCD = '00002902-0000-1000-8000-00805F9B34FB'

_LOGGER = logging.getLogger(__name__)


class DeviceInfo:
    def __init__(self, name, manufacturer, model, firmware_version):
        self.name = name
        self.manufacturer = manufacturer
        self.model = model
        self.firmware_version = firmware_version


class ThermometerData:
    humidity: typing.Optional[float] = None
    temperature: typing.Optional[float] = None


class MiTempCgg1Poller(object):
    """"
    A class to read data from Read data from Xiaomi Clear Grass Thermometer Hygrometer sensor with E-Ink.
    """
    _device_info: typing.Optional[DeviceInfo] = None
    _device_info_last_read:typing.Optional[datetime] = None
    _data_cache: typing.Optional[ThermometerData] = None
    _data_cache_last_read: typing.Optional[datetime] = None

    def __init__(self, mac, cache_timeout=60, notification_timeout=5.0):
        """
        Initialize a Poller for the given MAC address.
        """
        self._mac = mac
        self._peripheral = btle.Peripheral()
        self._cache_timeout = timedelta(seconds=cache_timeout)
        self._notification_timeout = notification_timeout
        self.lock = Lock()

    def _read_char(self, char_uuid, service_uuid=None):
        if not service_uuid:
            _LOGGER.debug('Reading characteristic %s of service %s', char_uuid, service_uuid)
            service = self._peripheral.getServiceByUUID(service_uuid)
            char = service.getCharacteristics(forUUID=char_uuid)[0]
        else:
            _LOGGER.debug('Reading characteristic %s', char_uuid)
            char = self._peripheral.getCharacteristics(uuid=char_uuid)[0]
        data = char.read()
        _LOGGER.debug('Read: %s', data)
        return data

    def device_info(self) -> DeviceInfo:
        if (not self._device_info) or (datetime.now() - timedelta(hours=24) > self._device_info_last_read):
            _LOGGER.debug('Getting device information of %s', self._mac)
            self._peripheral.connect(self._mac)
            try:
                name = self._read_char(_UUID_CHAR_NAME, service_uuid=_UUID_SERVICE_GENERIC_ACCESS).decode('utf-8')
                manufacturer = self._read_char(_UUID_CHAR_MANUFACTURER, service_uuid=_UUID_SERVICE_DEVICE_INFO).decode(
                    'utf-8')
                model = self._read_char(_UUID_CHAR_MODEL, service_uuid=_UUID_SERVICE_DEVICE_INFO).decode('utf-8')
                firmware_version = self._read_char(_UUID_CHAR_FIRMWARE, service_uuid=_UUID_SERVICE_DEVICE_INFO).decode(
                    'utf-8')

                self._device_info = DeviceInfo(name, manufacturer, model, firmware_version)
                self._device_info_last_read = datetime.now()
            finally:
                self._peripheral.disconnect()

        return self._device_info

    def fetch_data(self) -> ThermometerData:
        _LOGGER.debug('Fetching thermometer data.')
        try:
            device_info = self.device_info()
            self._peripheral.connect(self._mac)
            data_service = self._peripheral.getServiceByUUID(_UUID_SERVICE_DATA)
            data_char = data_service.getCharacteristics(forUUID=_UUID_CHAR_DATA)[0]
            cccd_desc = data_char.getDescriptors(_UUID_DESC_CCCD)[0]

            data = ThermometerData()
            delegate = MiTempCgg1Poller.MyDelegate(data)
            self._peripheral.setDelegate(delegate)

            cccd_desc.write(0x01.to_bytes(2, byteorder="little"), withResponse=True)
            self._peripheral.waitForNotifications(self._notification_timeout)
            cccd_desc.write(0x00.to_bytes(2, byteorder="little"), withResponse=True)
            return data

        finally:
            self._peripheral.disconnect()

    # TODO Battery
    def battery_level(self):
        return 0

    def firmware_version(self):
        return self.device_info().firmware_version

    def parameter_value(self, parameter, read_cached=True):
        """Return a value of one of the monitored paramaters.

        This method will try to retrieve the data from cache and only
        request it by bluetooth if no cached value is stored or the cache is
        expired.
        This behaviour can be overwritten by the "read_cached" parameter.
        """

        if parameter == MI_BATTERY:
            return self.battery_level()

        # Use the lock to make sure the cache isn't updated multiple times
        data: ThermometerData
        with self.lock:
            if read_cached and\
                    self._data_cache is not None and \
                    not (datetime.now() - self._cache_timeout > self._data_cache_last_read):
                data = self._data_cache
            else:
                self._data_cache = data = self.fetch_data()
                self._data_cache_last_read = datetime.now()

        return getattr(data, parameter)

    def clear_cache(self):
        """Manually force the cache to be cleared."""
        self._data_cache = None
        self._data_cache_last_read = None

    class MyDelegate(btle.DefaultDelegate):
        _data: ThermometerData

        def __init__(self, data: ThermometerData):
            self._data = data

        def handleNotification(self, handle, data):
            _LOGGER.debug('Notification from handle: %s with data: %s', handle, data)
            humidity_bytes = data[4:]
            temperature_bytes = data[:4][2:]
            self._data.humidity = int.from_bytes(humidity_bytes, byteorder='little') / 10.0
            self._data.temperature = int.from_bytes(temperature_bytes, byteorder='little') / 10.0
            _LOGGER.debug('T=%s,H=%s', self._data.temperature, self._data.humidity)