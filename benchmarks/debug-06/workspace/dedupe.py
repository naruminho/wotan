def duplicates(items):
    dups = []
    for i, a in enumerate(items):
        for j, b in enumerate(items):
            if i != j and a == b and a not in dups:
                dups.append(a)
    return dups
