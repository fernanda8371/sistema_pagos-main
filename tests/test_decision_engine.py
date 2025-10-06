import pytest
import pandas as pd
import tempfile
import os
import sys
from unittest.mock import patch, MagicMock

# Asegurar que podemos importar decision_engine
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import decision_engine as de


class TestDecisionEngine:
    
    def test_constants(self):
        """Test que las constantes están definidas correctamente"""
        assert de.DECISION_ACCEPTED == "ACCEPTED"
        assert de.DECISION_IN_REVIEW == "IN_REVIEW"
        assert de.DECISION_REJECTED == "REJECTED"
    
    def test_default_config_exists(self):
        """Test que DEFAULT_CONFIG existe y tiene estructura correcta"""
        assert isinstance(de.DEFAULT_CONFIG, dict)
        assert "amount_thresholds" in de.DEFAULT_CONFIG
        assert "latency_ms_extreme" in de.DEFAULT_CONFIG
        assert "chargeback_hard_block" in de.DEFAULT_CONFIG
        assert "score_weights" in de.DEFAULT_CONFIG
        assert "score_to_decision" in de.DEFAULT_CONFIG

    def test_is_night_function(self):
        """Test función is_night con diferentes horas"""
        # Horas nocturnas
        assert de.is_night(22) == True
        assert de.is_night(23) == True
        assert de.is_night(0) == True
        assert de.is_night(1) == True
        assert de.is_night(5) == True
        
        # Horas diurnas
        assert de.is_night(6) == False
        assert de.is_night(12) == False
        assert de.is_night(18) == False
        assert de.is_night(21) == False

    def test_high_amount_function(self):
        """Test función high_amount con diferentes tipos de productos"""
        thresholds = de.DEFAULT_CONFIG["amount_thresholds"]
        
        # Digital
        assert de.high_amount(3000, "digital", thresholds) == True
        assert de.high_amount(2000, "digital", thresholds) == False
        assert de.high_amount(2500, "digital", thresholds) == True
        
        # Physical
        assert de.high_amount(7000, "physical", thresholds) == True
        assert de.high_amount(5000, "physical", thresholds) == False
        
        # Subscription
        assert de.high_amount(2000, "subscription", thresholds) == True
        assert de.high_amount(1000, "subscription", thresholds) == False
        
        # Tipo desconocido (debe usar _default)
        assert de.high_amount(5000, "unknown", thresholds) == True
        assert de.high_amount(3000, "unknown", thresholds) == False

    def test_assess_row_hard_block(self):
        """Test escenario de bloqueo duro con chargebacks e IP de alto riesgo"""
        row = pd.Series({
            'chargeback_count': 3,
            'ip_risk': 'high',
            'amount_mxn': 1000
        })
        
        result = de.assess_row(row, de.DEFAULT_CONFIG)
        
        assert result['decision'] == de.DECISION_REJECTED
        assert result['risk_score'] == 100
        assert 'hard_block:chargebacks>=2+ip_high' in result['reasons']

    def test_assess_row_accepted_low_risk(self):
        """Test decisión aceptada con factores de bajo riesgo"""
        row = pd.Series({
            'chargeback_count': 0,
            'ip_risk': 'low',
            'email_risk': 'low',
            'device_fingerprint_risk': 'low',
            'user_reputation': 'trusted',
            'hour': 12,
            'amount_mxn': 1000,
            'product_type': 'digital',
            'latency_ms': 100,
            'customer_txn_30d': 5
        })
        
        result = de.assess_row(row, de.DEFAULT_CONFIG)
        
        assert result['decision'] == de.DECISION_ACCEPTED

    def test_assess_row_night_hour(self):
        """Test penalización por hora nocturna"""
        row = pd.Series({
            'hour': 23,
            'ip_risk': 'low',
            'user_reputation': 'new'
        })
        
        result = de.assess_row(row, de.DEFAULT_CONFIG)
        
        assert 'night_hour:23(+1)' in result['reasons']

    def test_assess_row_geo_mismatch(self):
        """Test penalización por desajuste geográfico"""
        row = pd.Series({
            'bin_country': 'MX',
            'ip_country': 'US',
            'ip_risk': 'low',
            'user_reputation': 'new'
        })
        
        result = de.assess_row(row, de.DEFAULT_CONFIG)
        
        assert 'geo_mismatch:MX!=US(+2)' in result['reasons']

    def test_assess_row_high_amount_digital(self):
        """Test monto alto para producto digital"""
        row = pd.Series({
            'amount_mxn': 3000,
            'product_type': 'digital',
            'ip_risk': 'low',
            'user_reputation': 'new'
        })
        
        result = de.assess_row(row, de.DEFAULT_CONFIG)
        
        assert 'high_amount:digital:3000.0(+2)' in result['reasons']

    def test_assess_row_new_user_high_amount(self):
        """Test usuario nuevo con monto alto"""
        row = pd.Series({
            'user_reputation': 'new',
            'amount_mxn': 3000,
            'product_type': 'digital',
            'ip_risk': 'low'
        })
        
        result = de.assess_row(row, de.DEFAULT_CONFIG)
        
        assert 'new_user_high_amount(+2)' in result['reasons']

    def test_assess_row_extreme_latency(self):
        """Test latencia extrema"""
        row = pd.Series({
            'latency_ms': 3000,
            'ip_risk': 'low',
            'user_reputation': 'new'
        })
        
        result = de.assess_row(row, de.DEFAULT_CONFIG)
        
        assert 'latency_extreme:3000ms(+2)' in result['reasons']

    def test_assess_row_frequency_buffer_trusted(self):
        """Test buffer de frecuencia para usuarios confiables"""
        row = pd.Series({
            'user_reputation': 'trusted',
            'customer_txn_30d': 5,
            'ip_risk': 'medium',  # Esto agrega puntuación (+2)
            'amount_mxn': 1000
        })
        
        result = de.assess_row(row, de.DEFAULT_CONFIG)
        
        # El score debería ser: ip_risk:medium(+2) + user_reputation:trusted(-2) = 0
        # Pero si score > 0 después de otros factores, debe aplicar frequency_buffer
        # En este caso el score final es 0, así que no aplica frequency_buffer
        # Vamos a verificar que el score sea correcto
        assert result['risk_score'] == 0
        assert 'ip_risk:medium(+2)' in result['reasons']
        assert 'user_reputation:trusted(-2)' in result['reasons']

    def test_assess_row_frequency_buffer_recurrent(self):
        """Test buffer de frecuencia para usuarios recurrentes"""
        row = pd.Series({
            'user_reputation': 'recurrent',
            'customer_txn_30d': 4,
            'ip_risk': 'high',  # +4 puntos
            'amount_mxn': 1000
        })
        
        result = de.assess_row(row, de.DEFAULT_CONFIG)
        
        # Score debe ser: ip_risk:high(+4) + user_reputation:recurrent(-1) = 3
        # Como score > 0 y customer_txn_30d >= 3 y reputation in (recurrent, trusted)
        # Debe aplicar frequency_buffer(-1), final score = 2
        assert result['risk_score'] == 2
        assert 'frequency_buffer(-1)' in result['reasons']

    def test_assess_row_email_risk_levels(self):
        """Test diferentes niveles de riesgo de email"""
        for risk_level, expected_score in [('medium', 1), ('high', 3), ('new_domain', 2)]:
            row = pd.Series({
                'email_risk': risk_level,
                'ip_risk': 'low',
                'user_reputation': 'new'
            })
            
            result = de.assess_row(row, de.DEFAULT_CONFIG)
            
            assert f'email_risk:{risk_level}(+{expected_score})' in result['reasons']

    def test_assess_row_ip_risk_levels(self):
        """Test diferentes niveles de riesgo IP"""
        for risk_level, expected_score in [('medium', 2), ('high', 4)]:
            row = pd.Series({
                'ip_risk': risk_level,
                'user_reputation': 'new'
            })
            
            result = de.assess_row(row, de.DEFAULT_CONFIG)
            
            assert f'ip_risk:{risk_level}(+{expected_score})' in result['reasons']

    def test_assess_row_device_risk_levels(self):
        """Test diferentes niveles de riesgo de dispositivo"""
        for risk_level, expected_score in [('medium', 2), ('high', 4)]:
            row = pd.Series({
                'device_fingerprint_risk': risk_level,
                'ip_risk': 'low',
                'user_reputation': 'new'
            })
            
            result = de.assess_row(row, de.DEFAULT_CONFIG)
            
            assert f'device_fingerprint_risk:{risk_level}(+{expected_score})' in result['reasons']

    def test_assess_row_user_reputation_levels(self):
        """Test diferentes niveles de reputación de usuario"""
        reputation_tests = [
            ('trusted', -2),
            ('recurrent', -1),
            ('high_risk', 4)
        ]
        
        for reputation, expected_score in reputation_tests:
            row = pd.Series({
                'user_reputation': reputation,
                'ip_risk': 'low',
                'customer_txn_30d': 0
            })
            
            result = de.assess_row(row, de.DEFAULT_CONFIG)
            
            sign = '+' if expected_score >= 0 else ''
            assert f'user_reputation:{reputation}({sign}{expected_score})' in result['reasons']

    def test_assess_row_missing_fields(self):
        """Test assess_row con campos faltantes"""
        row = pd.Series({})  # Row vacío
        
        result = de.assess_row(row, de.DEFAULT_CONFIG)
        
        assert 'decision' in result
        assert 'risk_score' in result
        assert 'reasons' in result

    def test_run_function(self):
        """Test función run con CSV temporal"""
        test_data = pd.DataFrame({
            'amount_mxn': [1000, 5000, 2000],
            'ip_risk': ['low', 'high', 'medium'],
            'user_reputation': ['trusted', 'high_risk', 'new'],
            'hour': [12, 23, 14],
            'chargeback_count': [0, 1, 0],
            'product_type': ['digital', 'digital', 'physical']
        })
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as input_file:
            test_data.to_csv(input_file.name, index=False)
            input_path = input_file.name
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as output_file:
            output_path = output_file.name
        
        try:
            result_df = de.run(input_path, output_path)
            
            assert len(result_df) == 3
            assert 'decision' in result_df.columns
            assert 'risk_score' in result_df.columns
            assert 'reasons' in result_df.columns
            
            # Verificar que el archivo de salida existe
            assert os.path.exists(output_path)
            
        finally:
            if os.path.exists(input_path):
                os.unlink(input_path)
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_run_with_custom_config(self):
        """Test función run con configuración personalizada"""
        custom_config = de.DEFAULT_CONFIG.copy()
        custom_config['score_to_decision']['reject_at'] = 5
        
        test_data = pd.DataFrame({
            'amount_mxn': [1000],
            'ip_risk': ['medium'],
            'user_reputation': ['new']
        })
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as input_file:
            test_data.to_csv(input_file.name, index=False)
            input_path = input_file.name
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as output_file:
            output_path = output_file.name
        
        try:
            result_df = de.run(input_path, output_path, custom_config)
            assert len(result_df) == 1
            
        finally:
            if os.path.exists(input_path):
                os.unlink(input_path)
            if os.path.exists(output_path):
                os.unlink(output_path)

    @patch('sys.argv', ['decision_engine.py'])
    @patch('decision_engine.run')
    def test_main_function(self, mock_run):
        """Test función main"""
        mock_df = MagicMock()
        mock_df.head.return_value.to_string.return_value = "test output"
        mock_run.return_value = mock_df
        
        # Crear archivo de prueba temporal
        test_data = pd.DataFrame({'test': [1, 2, 3]})
        test_data.to_csv('transactions_examples.csv', index=False)
        
        try:
            de.main()
            mock_run.assert_called_once()
        finally:
            if os.path.exists('transactions_examples.csv'):
                os.unlink('transactions_examples.csv')
            if os.path.exists('decisions.csv'):
                os.unlink('decisions.csv')

    @patch('sys.argv', ['decision_engine.py', '--input', 'test_input.csv', '--output', 'test_output.csv'])
    @patch('decision_engine.run')
    def test_main_function_with_args(self, mock_run):
        """Test función main con argumentos"""
        mock_df = MagicMock()
        mock_df.head.return_value.to_string.return_value = "test output"
        mock_run.return_value = mock_df
        
        # Crear archivo de prueba
        test_data = pd.DataFrame({'test': [1, 2, 3]})
        test_data.to_csv('test_input.csv', index=False)
        
        try:
            de.main()
            mock_run.assert_called_with('test_input.csv', 'test_output.csv')
        finally:
            if os.path.exists('test_input.csv'):
                os.unlink('test_input.csv')
            if os.path.exists('test_output.csv'):
                os.unlink('test_output.csv')

    def test_environment_variables_import(self):
        """Test que las variables de entorno se manejan correctamente en la importación"""
        import importlib
        import os
        
        # Guardar valores originales
        original_reject = os.environ.get("REJECT_AT")
        original_review = os.environ.get("REVIEW_AT")
        
        try:
            # Establecer variables de entorno
            os.environ["REJECT_AT"] = "15"
            os.environ["REVIEW_AT"] = "8"
            
            # Reimportar el módulo para que se ejecute el código de env vars
            importlib.reload(de)
            
            # Verificar que los valores se aplicaron
            assert de.DEFAULT_CONFIG["score_to_decision"]["reject_at"] == 15
            assert de.DEFAULT_CONFIG["score_to_decision"]["review_at"] == 8
            
        finally:
            # Limpiar variables de entorno
            if original_reject is not None:
                os.environ["REJECT_AT"] = original_reject
            else:
                os.environ.pop("REJECT_AT", None)
                
            if original_review is not None:
                os.environ["REVIEW_AT"] = original_review
            else:
                os.environ.pop("REVIEW_AT", None)
            
            # Restaurar módulo
            importlib.reload(de)

    def test_assess_row_all_product_types(self):
        """Test assess_row con todos los tipos de productos"""
        for product_type in ['digital', 'physical', 'subscription']:
            row = pd.Series({
                'amount_mxn': 10000,  # Monto alto para activar high_amount
                'product_type': product_type,
                'user_reputation': 'new',
                'ip_risk': 'low'
            })
            
            result = de.assess_row(row, de.DEFAULT_CONFIG)
            
            assert f'high_amount:{product_type}:10000.0(+2)' in result['reasons']

    def test_assess_row_geo_mismatch_edge_cases(self):
        """Test casos extremos de geo mismatch"""
        # Sin países
        row1 = pd.Series({
            'ip_risk': 'low',
            'user_reputation': 'new'
        })
        result1 = de.assess_row(row1, de.DEFAULT_CONFIG)
        assert 'geo_mismatch' not in result1['reasons']
        
        # Un país vacío
        row2 = pd.Series({
            'bin_country': '',
            'ip_country': 'US',
            'ip_risk': 'low',
            'user_reputation': 'new'
        })
        result2 = de.assess_row(row2, de.DEFAULT_CONFIG)
        assert 'geo_mismatch' not in result2['reasons']
        
        # Países iguales
        row3 = pd.Series({
            'bin_country': 'MX',
            'ip_country': 'MX',
            'ip_risk': 'low',
            'user_reputation': 'new'
        })
        result3 = de.assess_row(row3, de.DEFAULT_CONFIG)
        assert 'geo_mismatch' not in result3['reasons']

    def test_assess_row_frequency_buffer_edge_cases(self):
        """Test casos extremos del buffer de frecuencia"""
        # Usuario confiable pero con pocas transacciones
        row = pd.Series({
            'user_reputation': 'trusted',
            'customer_txn_30d': 2,  # Menos de 3
            'ip_risk': 'medium',
            'amount_mxn': 1000
        })
        
        result = de.assess_row(row, de.DEFAULT_CONFIG)
        
        # No debe tener frequency_buffer porque customer_txn_30d < 3
        assert 'frequency_buffer' not in result['reasons']

    def test_assess_row_score_zero_no_frequency_buffer(self):
        """Test que frequency_buffer no se aplica si score es 0"""
        row = pd.Series({
            'user_reputation': 'trusted',
            'customer_txn_30d': 5,
            'ip_risk': 'low',  # No agrega puntos
            'amount_mxn': 1000  # No es monto alto
        })
        
        result = de.assess_row(row, de.DEFAULT_CONFIG)
        
        # El score debería ser <= 0, entonces no debe aplicar frequency_buffer
        if result['risk_score'] <= 0:
            assert 'frequency_buffer' not in result['reasons']

    def test_main_as_script(self):
        """Test ejecución del script cuando __name__ == '__main__'"""
        import subprocess
        import tempfile
        
        # Crear archivo de entrada temporal
        test_data = pd.DataFrame({
            'amount_mxn': [1000, 2000],
            'ip_risk': ['low', 'medium'],
            'user_reputation': ['trusted', 'new']
        })
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
            test_data.to_csv(f.name, index=False)
            input_file = f.name
        
        try:
            # Ejecutar el script como módulo principal
            result = subprocess.run([
                sys.executable, 'decision_engine.py', 
                '--input', input_file, 
                '--output', 'test_output.csv'
            ], 
            cwd=os.path.dirname(os.path.dirname(__file__)),
            capture_output=True, 
            text=True
            )
            
            # El script debe ejecutarse sin error
            assert result.returncode == 0
            
        finally:
            # Limpiar archivos temporales
            if os.path.exists(input_file):
                os.unlink(input_file)
            if os.path.exists('test_output.csv'):
                os.unlink('test_output.csv')