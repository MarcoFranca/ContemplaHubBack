import unittest

from app.services.meta_leads_service import _parse_meta_field_data, normalize_phone


class NormalizePhoneTests(unittest.TestCase):
    def test_remove_ddi_55_only_for_12_digits(self) -> None:
        self.assertEqual(normalize_phone("552234567890"), "2234567890")

    def test_remove_ddi_55_only_for_13_digits(self) -> None:
        self.assertEqual(normalize_phone("p:+5522999679925"), "22999679925")

    def test_keep_brazilian_ddd_55_when_number_has_11_digits(self) -> None:
        self.assertEqual(normalize_phone("55999999999"), "55999999999")

    def test_keep_number_without_ddi(self) -> None:
        self.assertEqual(normalize_phone("22999679925"), "22999679925")


class ParseMetaFieldDataTests(unittest.TestCase):
    def test_parse_contact_and_custom_fields_conservatively(self) -> None:
        parsed = _parse_meta_field_data(
            [
                {"name": "full_name", "values": ["Maria da Silva"]},
                {"name": "email", "values": ["maria@example.com"]},
                {"name": "phone_number", "values": ["p:+5522999679925"]},
                {"name": "objetivo_consorcio", "values": ["Comprar imóvel"]},
                {"name": "valor_mensal_pretendido", "values": ["R$ 800"]},
                {"name": "renda_mensal", "values": ["R$ 5.000"]},
            ]
        )

        self.assertEqual(parsed["nome"], "Maria da Silva")
        self.assertEqual(parsed["email"], "maria@example.com")
        self.assertEqual(parsed["telefone"], "22999679925")
        self.assertEqual(parsed["custom_fields"]["objetivo_consorcio_raw"], "Comprar imóvel")
        self.assertEqual(parsed["custom_fields"]["valor_mensal_pretendido_raw"], "R$ 800")
        self.assertEqual(parsed["custom_fields"]["renda_mensal_raw"], "R$ 5.000")
        self.assertEqual(parsed["raw_field_values"]["phone_number"], "p:+5522999679925")


if __name__ == "__main__":
    unittest.main()
