from clearbill.explain import verify_amounts


def test_verify_amounts_flags_invented_numbers():
    facts = {"bill_total_due": 232.0, "findings": [{"detail": "asks $1,200.50 for a visit", "amount": 200.0}]}
    assert verify_amounts("You owe $232.00, not $200.", facts) == []
    assert verify_amounts("The bill shows $1,200.50.", facts) == []
    assert verify_amounts("You were overcharged $199.99.", facts) == [199.99]
