count = int(input())
names = []
for _ in range(count):
    names.append(input().strip())
for name in sorted(names):
    print(name)
