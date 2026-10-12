status = "approved"
raw_command = f'curl -X POST "http://192.168.2.12:8000/api/agents/evai-callback" -H "Content-Type: application/json" -d "{{\\"status\\": \\"{status}\\"}}"'
print(raw_command)
