words = input().split()
total = 0
for word in words:
    total = total + len(word)
print(f"{total / len(words):.2f}")
