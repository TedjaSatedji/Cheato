# Quiz Cheatsheet — question types & how to answer them

## 1. What the bot handles today

| Moodle question type | Looks like | Bot | Why |
|---|---|---|---|
| Multiple choice (pick 1) | radio buttons | ✅ AUTO | clicks the radio matching the model's letter |
| True / False | radio buttons | ✅ AUTO | same radio path |
| Multiple choice (pick MANY) | checkboxes | ❌ manual | bot only clicks radios |
| Matching | dropdown selects | ❌ manual | no dropdown logic |
| Short answer | text input | ❌ manual | bot can't fill text boxes |
| Numerical | text input | ❌ manual | same |
| Essay | textarea | ❌ manual | free text |
| Drag & drop / Cloze (mixed) | drag zones + inputs | ❌ manual | mixed elements |

**Rule of thumb:** if the answer options are *radio buttons*, press `a` (or let `AUTO_ANSWER=1` do it) and the bot picks.
Anything else — answer it by hand using the facts below; the bot stays quiet and lets you drive.

## 2. Driving the bot per type

- **Radio question** → `a` = ask model + click. `q` = stop. (`AUTO_ANSWER=1` = no key needed.)
- **Checkbox question** → click the boxes yourself. When in doubt, first option is usually wrong; read all boxes before picking.
- **Matching / dropdown** → work through each dropdown; match definitions to terms.
- **Short answer / numerical** → type the answer; the facts below give you the exact wording lecturers expect.
- **Essay** → 3–4 sentences: definition → how it works → one example → one pro/con.

## 3. Facts per topic (from your materials/)

### Array (pertemuan 3)
- Stores elements **contiguous in memory, same type** (homogeneous), **fixed size**.
- Indexing: **zero-based** in most languages (first element = index 0).
- Declaration in C: `int arr[5];` — access second element: `arr[1]`.
- Basic ops: initialize `int arr[5] = {1,2,3,4,5};`, read `arr[1]`, write `arr[2] = 10;`, traverse with a `for` loop, linear search.
- Pros: **O(1) direct access by index**, simple, memory-efficient for static data.
- Cons: **fixed size**, wasted space if oversized, insert/delete in the middle is **O(n)** (elements shift).

### Pointer & Linked List (pertemuan 4)
- Pointer = **variable that stores the address of another variable**.
- `&` takes an address (`int *p = &a;`), `*` dereferences (read/write through the pointer: `*ptr = 10;`).
- Uninitialized pointers are dangerous (random address → runtime error).
- Benefits: dynamic memory (`malloc`, `calloc`, `free`), build complex structures (list, stack, queue, tree, graph), pass by reference (cheaper than copying), indirect data manipulation, hardware/OS access.
- Linked list = nodes of **data + pointer to next**; elements **not contiguous** in memory.
- Types: **singly** (next only), **doubly** (next + prev), **circular** (last → first).
- vs Array: dynamic size, fast insert/delete (pointer rewiring, no shifting), memory allocated on demand, less fragmentation. Cost: access is **O(n)**, extra memory per pointer, harder to implement.

### Circular & Doubly Linked List (pertemuan 5)
- **Doubly**: each node has `next` **and** `prev` → traverse forward AND backward. Deletion is easier (direct access to the previous node). Node: `struct Node { int data; struct Node* next; struct Node* prev; };`
- Doubly insert-at-head: new node's `next` = old head, its `prev` = NULL, old head's `prev` = new node, head = new node.
- **Circular**: last node points **back to the first** (no NULL end). Fits repeating processes (music player, round-robin). Traversal stops when `current` **returns to head**.
- Circular delete-head: find the tail, point `tail->next` to `head->next`, advance head, free the node.

### Stack (pertemuan 6)
- **LIFO** (Last In, First Out) — like a stack of plates. ADT with operations limited to the top.
- Ops: **push** (add on top), **pop** (remove top), **peek** (read top without removing), **isEmpty** (`top == -1` array / `head == NULL` list), **size**.
- `top` increments on push, decrements on pop.
- Array implementation: **fixed capacity** → **stack overflow** (push when full), **stack underflow** (pop when empty).
- Linked-list implementation: capacity limited only by memory, but more complex.
- Applications: **function calls / recursion** (call stack), **expression evaluation** (infix → postfix), **undo/redo** (two stacks), **DFS** graph traversal, **balanced brackets** check, OS context switching.
- Pros: simple, push/pop **O(1)**. Cons: only top is accessible, array version has fixed capacity, LIFO is wrong for random access / FIFO needs.

## 4. Complexities at a glance

| Operation | Array | Linked list | Stack |
|---|---|---|---|
| Access by index / find | O(1) / O(n) | O(n) | top only |
| Insert / delete middle | O(n) (shift) | O(1) (rewire pointers) | — |
| Push / pop | — | — | O(1) |
| Memory | tight, fixed | extra per node (pointers) | depends on impl |
