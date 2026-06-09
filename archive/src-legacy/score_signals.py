import json

FIELDS = ["durability", "leverage", "alignment", "accessibility", "novelty"]

def get_score(field):
    while True:
        value = input(f"{field.capitalize()} score (1-5): ")

        try:
            score = int(value)
            if 1 <= score <= 5:
                return score
        except ValueError:
            pass

        print("Please enter a number from 1 to 5.")

with open("data/signals.json", "r") as f:
    signals = json.load(f)

for i, signal in enumerate(signals, start=1):
    print(f"{i}. {signal['title']}")

choice = int(input("\nChoose signal number to score: ")) - 1
signal = signals[choice]

print(f"\nScoring: {signal['title']}\n")

for field in FIELDS:
    signal["scores"][field] = get_score(field)

with open("data/signals.json", "w") as f:
    json.dump(signals, f, indent=2)

print(f"\nScored: {signal['title']}")
