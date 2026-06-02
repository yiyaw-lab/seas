import subprocess

print("\n🌊 SEAS Weekly Run\n")

print("Step 1: Checking signal status...\n")

subprocess.run(["python", "src/week.py"])

print("\n" + "=" * 60)
print("SEAS workflow complete.")
print("=" * 60)
