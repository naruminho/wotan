def read_names(path="names.txt"):
    data = open(path, "rb").read()
    return [line for line in data.decode("ascii", errors="replace").splitlines() if line]
