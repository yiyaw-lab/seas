def generate_experiments(signal):
    title = signal["title"]
    capability = signal.get(
        "possible_capability_unlocked",
        "Unknown Capability"
    )

    return [
        {
            "type": "Fast Prototype",
            "title": f"Build a tiny working demo of {title}",
            "capability": capability,
            "artifact": f"A minimal prototype demonstrating {capability}",
            "completion": "A working demo exists and can be explained in under 2 minutes"
        },
        {
            "type": "Evaluation Experiment",
            "title": f"Test whether {title} actually improves a real workflow",
            "capability": f"Evaluating {capability}",
            "artifact": "A before/after comparison or small benchmark",
            "completion": "You can clearly say whether the signal is useful, overhyped, or promising"
        },
        {
            "type": "Public Builder Artifact",
            "title": f"Create a build-in-public experiment around {title}",
            "capability": f"Communicating and applying {capability}",
            "artifact": "A GitHub repo, demo note, or public post",
            "completion": "Someone else could understand and reference the experiment"
        }
    ]
