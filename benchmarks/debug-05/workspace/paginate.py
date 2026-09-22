def page(items, page_no, size):
    start = page_no * size
    return items[start : start + size + 1]
