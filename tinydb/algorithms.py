"""Searching and sorting, written by hand.

binary_search finds where a value belongs in a sorted list in O(log n) steps.
merge_sort sorts a list in O(n log n) steps, and it's stable.

The B-tree (btree.py) runs binary_search inside every node, and Table.sort_by
uses merge_sort when there's no B-tree index to read the records in order.
"""


def binary_search(items, target):
    """Return the first position in the sorted list items where target could go.

    If target is in the list, that's the position of its first copy. If it isn't,
    it's where you'd insert it to keep the list sorted. (Same as bisect.bisect_left.)
    """
    low, high = 0, len(items)
    while low < high:
        middle = (low + high) // 2
        if items[middle] < target:
            low = middle + 1  # target belongs to the right of middle
        else:
            high = middle  # target belongs at middle or to its left
    return low


def merge_sort(items, key=None, reverse=False):
    """Return a new sorted list. Stable: items that tie keep their original order.

    key works like it does for sorted(): a function that picks what to sort by.
    """
    if key is None:
        key = _same
    # Work out each item's sort key once, up front, and carry it in a pair.
    pairs = [(key(item), item) for item in items]
    return [item for _, item in _sort_pairs(pairs, reverse)]


def _same(item):
    return item


def _sort_pairs(pairs, reverse):
    """Split the list in half, sort each half, then merge the two sorted halves."""
    if len(pairs) <= 1:
        return pairs
    middle = len(pairs) // 2
    left = _sort_pairs(pairs[:middle], reverse)
    right = _sort_pairs(pairs[middle:], reverse)
    return _merge(left, right, reverse)


def _merge(left, right, reverse):
    """Combine two sorted lists into one sorted list."""
    merged = []
    i = j = 0
    while i < len(left) and j < len(right):
        left_key, right_key = left[i][0], right[j][0]
        # Take from the right only when it must come first. On a tie the left item
        # wins, which is what keeps the sort stable.
        right_first = right_key > left_key if reverse else right_key < left_key
        if right_first:
            merged.append(right[j])
            j += 1
        else:
            merged.append(left[i])
            i += 1
    # One side ran out. Whatever is left on the other side is already sorted.
    merged.extend(left[i:])
    merged.extend(right[j:])
    return merged
