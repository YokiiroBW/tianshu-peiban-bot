"""Native QWeather response shapes; no account/network calls."""

import unittest
from tianshu.capabilities.weather.protocol import host_url, current_view
from tianshu.capabilities.contracts import Fault


class WeatherProtocol(unittest.TestCase):
    def test_account_host_and_current_v1_units_attribution(self):
        self.assertEqual(host_url("tenant.re.qweatherapi.com"), "https://tenant.re.qweatherapi.com")
        with self.assertRaises(Fault):
            host_url("https://tenant.qweatherapi.com@elsewhere.invalid")
        result = current_view(
            {
                "temperature": {"unit": "°C", "value": 22},
                "feelsLike": {"unit": "°C", "value": 23},
                "condition": {"text": "晴", "code": "100"},
                "wind": {"scale": 2},
                "metadata": {"attributions": ["QWeather"]},
            }
        )
        self.assertIsNone(result["observed_at"])
        self.assertEqual(result["attributions"], ["QWeather"])
        self.assertEqual(result["temp"], "22")
