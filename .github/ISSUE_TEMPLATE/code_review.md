---
name: Code Review
about: Use this template for code review and comment of a specific source file.
title: "[Code Review] latte.py (edit this)"
labels: code review
assignees: lbluque, qchempku2017
---
Make sure that a code review issue for the source file you are about to create
one does not already exist. Please edit the above fields based on the file you
are opening this issue for. 

| File Name | Module Path | Authors|
|-----------|-------------|--------|
| latte.py  |smol.oatmilk |Mr. Coder|

## Code Review
We use *code review* issues to open up a pinned location to discuss a specific
source file.

### Source file summary
Please add a brief summary of what the purpose of the code in the source file
is. (A good and short docstring can be just copy and pasted.)

#### Use github features when possible please!

**Point to specific lines in code**

If you are mentioning a set of specific lines in code already implemented, it
may come in handy to add a
[permanent link](https://docs.github.com/en/enterprise/2.21/user/github/managing-your-work-on-github/creating-a-permanent-link-to-a-code-snippet) to it.

**Format suggested code niceley**

```python
def roast(beans, *flavoring_oils):
    for oil in flavoring_oils:
        beans.add_oil(oils)
    return np.trapz(beans.get_heat_vector())
```

**Use other handy features too**

Using many of other handy features offered, can help improve discussion.
https://docs.github.com/en