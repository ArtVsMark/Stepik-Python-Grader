def test_single_file_run_exits_zero(self):
    # Use a temporary directory that can be deleted
    with tempfile.TemporaryDirectory() as temp_dir:
        # Run pytest with the temporary directory
        pytest_args = ['--basetemp', temp_dir]
        self.assertEqual(pytest.main(pytest_args), 0)
