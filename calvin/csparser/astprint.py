import visitor
import astnode as ast

class BracePrinter(object):

    def _visit(self, node, preorder=None, inorder=None, postorder=None):
        if preorder: preorder(node)
        left, last = node.children[0:-1], node.children[-1:]
        if not left:
            if last: self.visit(last[0])
            if inorder: inorder(node)
        else:
            for child in left:
                self.visit(child)
                if inorder: inorder(node)
            self.visit(last[0])
        if postorder: postorder(node)

    @staticmethod
    def _printval(n):
        print "n.type",

    @visitor.on('node')
    def visit(self, node):
        pass

    @visitor.when(ast.Node)
    def visit(self, node):
        def f(n): print "( {}".format(node.__class__.__name__),
        def g(n): print ")",
        self._visit(node, preorder=f, postorder=g)

    @visitor.when(ast.Id)
    def visit(self, node):
        print node.ident,

    @visitor.when(ast.Value)
    def visit(self, node):
        print node.value,

    @visitor.when(ast.Assignment)
    def visit(self, node):
        def f(n): print "( {} {} {}".format(node.__class__.__name__, node.ident, node.actor_type),
        def g(n): print ")",
        self._visit(node, preorder=f, postorder=g)

    @visitor.when(ast.Port)
    def visit(self, node):
        print "( {} {}.{} )".format(node.__class__.__name__, node.actor, node.port),

    @visitor.when(ast.InternalPort)
    def visit(self, node):
        print "( {} .{} )".format(node.__class__.__name__, node.port),

    @visitor.when(ast.Component)
    def visit(self, node):
        def f(n): print "( {} {}".format(node.__class__.__name__, node.name),
        def g(n): print ")",
        self._visit(node, preorder=f, postorder=g)
