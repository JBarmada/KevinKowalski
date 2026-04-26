import ast
import pathlib
import builtins

import networkx as nx
import matplotlib.pyplot as plt


def hi(name):
    print(f'hi, {name}')


def _get_name(node: ast.expr):
    if isinstance(node, ast.Name): # base case
        return node.id
    elif isinstance(node, ast.Attribute): # recursive case
        return f'{_get_name(node.value)}.{node.attr}'
    else:
        # we really shouldn't get here. this is a placeholder to check if we do. could replace with return "?" or something
        # oh actually we should get here with something like foo()().
        return "?"
        # raise TypeError(f"got ast node which wasn't ast.Name or ast.Attribute. the type was {type(node)}")


def get_dependency_graph(tree) -> nx.DiGraph:
    """
    Get the dependency graph of all functions in a file.
    Issues: if run across different files, it may create multiple nodes for the same function. Since it may be just function_name in one module but module.function_name in another. I guess an LSP is the right way to do this for real. We need to get every function which is defined, and make a node for it.
    """
    G = nx.DiGraph()

    builtins_set = set(dir(builtins))

    for function in ast.walk(tree):
        if isinstance(function, ast.FunctionDef):
            function_name = function.name

            G.add_node(function_name)

            for dependency in ast.walk(function):
                if isinstance(dependency, ast.Call):  # a ast.Call will also pick up instantiations of objects, which is what we want.
                    dependency_name = _get_name(dependency.func)

                    if dependency_name in builtins_set: # calls to builtins aren't relevant for our purposes
                        continue

                    G.add_node(dependency_name)
                    G.add_edge(function_name, dependency_name)
    
    return G


file = pathlib.Path(__file__)
text = file.read_text()

tree = ast.parse(text)

G = get_dependency_graph(tree)

pos = nx.nx_pydot.graphviz_layout(G, prog='dot')
nx.draw(G, pos, with_labels=True)
plt.show()
