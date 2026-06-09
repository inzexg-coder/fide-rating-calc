import re

with open('backend/fetchers.py', 'r') as f:
    content = f.read()

# Fix params dict
old_params = '''        params = {
            "max": 500,
            "perPage": 500,
            "pgnInJson": True,
            "opening": False,
            "accuracy": True,
            "evals": False,
            "moves": False,
        }'''
new_params = '''        params = {
            "max": 500,
            "perPage": 500,
            "pgnInJson": "true",
            "opening": "false",
            "accuracy": "true",
            "evals": "false",
            "moves": "false",
        }'''
content = content.replace(old_params, new_params)

with open('backend/fetchers.py', 'w') as f:
    f.write(content)
print("Fixed")
