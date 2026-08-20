count = int(input())
scores = []
for _ in range(count):
    scores.append(int(input()))
for score in scores:
    if score >= 60:
        print(score)
