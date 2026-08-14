def test_single_file_run_exits_zero(self):
    pytest.run_tests(args=['--basetemp'], exit=False)
