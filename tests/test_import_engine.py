def test_import_engine():
    import decision_engine as de
    assert isinstance(de.DEFAULT_CONFIG, dict)
    
def test_basic_functionality():
    """Basic smoke test for decision engine"""
    import decision_engine as de
    import pandas as pd
    
    # Test basic row assessment
    row = pd.Series({
        'amount_mxn': 1000,
        'ip_risk': 'low',
        'user_reputation': 'trusted',
        'hour': 12
    })
    
    result = de.assess_row(row, de.DEFAULT_CONFIG)
    
    assert 'decision' in result
    assert 'risk_score' in result
    assert 'reasons' in result
    assert result['decision'] in ['ACCEPTED', 'IN_REVIEW', 'REJECTED']
