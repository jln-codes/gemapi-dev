import unittest

from digues_app.crs import (
    BusinessCRSError,
    EPSG_2154,
    EPSG_3950,
    QUALIFIED_BUSINESS_CRS,
    qualified_business_crs,
)


class BusinessCRSTest(unittest.TestCase):
    def test_only_qualified_projected_metric_crs_are_available(self):
        self.assertEqual(set(QUALIFIED_BUSINESS_CRS), {2154, *range(3942, 3951)})
        self.assertEqual(qualified_business_crs("EPSG:3950"), EPSG_3950)
        self.assertEqual(qualified_business_crs(2154), EPSG_2154)
        for srid in range(3942, 3951):
            self.assertEqual(qualified_business_crs(f"EPSG:{srid}").name, f"RGF93 v1 / CC{srid - 3900}")
        self.assertTrue(all(crs.projected for crs in QUALIFIED_BUSINESS_CRS.values()))
        self.assertTrue(all(crs.linear_unit == "metre" for crs in QUALIFIED_BUSINESS_CRS.values()))

    def test_unqualified_or_exchange_crs_is_rejected(self):
        for value in (4326, 3857, 3941, 3951, 5490, 9794, 9842, 999999, True, "Lambert-93"):
            with self.subTest(value=value), self.assertRaises(BusinessCRSError):
                qualified_business_crs(value)


if __name__ == "__main__":
    unittest.main()
