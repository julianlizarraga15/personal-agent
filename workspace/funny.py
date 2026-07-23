#!/usr/bin/env python3
import random
import sys
import time

JOKES = [
    "Why don't scientists trust atoms? Because they make up everything.",
    "I told my computer I needed a break, and now it won't stop sending me Kit Kat ads.",
    "Why did the scarecrow win an award? He was outstanding in his field.",
    "I asked the librarian for books on paranoia. She whispered, 'They're right behind you.'",
]


def type_out(text, min_delay=0.01, max_delay=0.06):
    for character in text:
        sys.stdout.write(character)
        sys.stdout.flush()
        time.sleep(random.uniform(min_delay, max_delay))
    print()


def fake_loading(message="Thinking..."):
    for progress in range(21):
        bar = "[" + "#" * progress + " " * (20 - progress) + "]"
        sys.stdout.write(f"\r{message} {bar} {progress * 5}%")
        sys.stdout.flush()
        time.sleep(0.08 + random.random() * 0.06)
    print()


def main():
    print("Welcome to the Sarcastic Joke Bot 🤖")
    time.sleep(0.5)
    fake_loading("Consulting the funny bone")
    type_out("Delivering joke:")
    type_out(random.choice(JOKES), 0.01, 0.05)
    time.sleep(0.4)
    type_out("Reaction: " + random.choice(["😏", "😂", "😐", "🤦‍♀️"]), 0.02, 0.07)
    time.sleep(0.4)
    type_out("Moral of the story: Don't ask your toaster for life advice.", 0.01, 0.05)


if __name__ == "__main__":
    main()
