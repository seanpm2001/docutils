# $Id$
# Author: David Goodger <goodger@python.org>
# Maintainer: docutils-develop@lists.sourceforge.net
# Copyright: This module has been placed in the public domain.

"""
Docutils document tree element class library.

Classes in CamelCase are abstract base classes or auxiliary classes. The one
exception is `Text`, for a text (PCDATA) node; uppercase is used to
differentiate from element classes.  Classes in lower_case_with_underscores
are element classes, matching the XML element generic identifiers in the DTD_.

The position of each node (the level at which it can occur) is significant and
is represented by abstract base classes (`Root`, `Structural`, `Body`,
`Inline`, etc.).  Certain transformations will be easier because we can use
``isinstance(node, base_class)`` to determine the position of the node in the
hierarchy.

.. _DTD: https://docutils.sourceforge.io/docs/ref/docutils.dtd
"""

__docformat__ = 'reStructuredText'

from collections import Counter
import re
import sys
import unicodedata
import warnings
# import xml.dom.minidom as dom # -> conditional import in Node.asdom()
#                                    and document.asdom()

# import docutils.transforms # -> conditional import in document.__init__()


# ==============================
#  Functional Node Base Classes
# ==============================

class Node:
    """Abstract base class of nodes in a document tree."""

    parent = None
    """Back-reference to the Node immediately containing this Node."""

    source = None
    """Path or description of the input source which generated this Node."""

    line = None
    """The line number (1-based) of the beginning of this Node in `source`."""

    _document = None

    @property
    def document(self):
        """Return the `document` root node of the tree containing this Node.
        """
        try:
            return self._document or self.parent.document
        except AttributeError:
            return None

    @document.setter
    def document(self, value):
        self._document = value

    def __bool__(self):
        """
        Node instances are always true, even if they're empty.  A node is more
        than a simple container.  Its boolean "truth" does not depend on
        having one or more subnodes in the doctree.

        Use `len()` to check node length.
        """
        return True

    def asdom(self, dom=None):
        """Return a DOM **fragment** representation of this Node."""
        if dom is None:
            import xml.dom.minidom as dom
        domroot = dom.Document()
        return self._dom_node(domroot)

    def pformat(self, indent='    ', level=0):
        """
        Return an indented pseudo-XML representation, for test purposes.

        Override in subclasses.
        """
        raise NotImplementedError

    def copy(self):
        """Return a copy of self."""
        raise NotImplementedError

    def deepcopy(self):
        """Return a deep copy of self (also copying children)."""
        raise NotImplementedError

    def astext(self):
        """Return a string representation of this Node."""
        raise NotImplementedError

    def setup_child(self, child):
        child.parent = self
        if self.document:
            child.document = self.document
            if child.source is None:
                child.source = self.document.current_source
            if child.line is None:
                child.line = self.document.current_line

    def walk(self, visitor):
        """
        Traverse a tree of `Node` objects, calling the
        `dispatch_visit()` method of `visitor` when entering each
        node.  (The `walkabout()` method is similar, except it also
        calls the `dispatch_departure()` method before exiting each
        node.)

        This tree traversal supports limited in-place tree
        modifications.  Replacing one node with one or more nodes is
        OK, as is removing an element.  However, if the node removed
        or replaced occurs after the current node, the old node will
        still be traversed, and any new nodes will not.

        Within ``visit`` methods (and ``depart`` methods for
        `walkabout()`), `TreePruningException` subclasses may be raised
        (`SkipChildren`, `SkipSiblings`, `SkipNode`, `SkipDeparture`).

        Parameter `visitor`: A `NodeVisitor` object, containing a
        ``visit`` implementation for each `Node` subclass encountered.

        Return true if we should stop the traversal.
        """
        stop = False
        visitor.document.reporter.debug(
            'docutils.nodes.Node.walk calling dispatch_visit for %s'
            % self.__class__.__name__)
        try:
            try:
                visitor.dispatch_visit(self)
            except (SkipChildren, SkipNode):
                return stop
            except SkipDeparture:           # not applicable; ignore
                pass
            children = self.children
            try:
                for child in children[:]:
                    if child.walk(visitor):
                        stop = True
                        break
            except SkipSiblings:
                pass
        except StopTraversal:
            stop = True
        return stop

    def walkabout(self, visitor):
        """
        Perform a tree traversal similarly to `Node.walk()` (which
        see), except also call the `dispatch_departure()` method
        before exiting each node.

        Parameter `visitor`: A `NodeVisitor` object, containing a
        ``visit`` and ``depart`` implementation for each `Node`
        subclass encountered.

        Return true if we should stop the traversal.
        """
        call_depart = True
        stop = False
        visitor.document.reporter.debug(
            'docutils.nodes.Node.walkabout calling dispatch_visit for %s'
            % self.__class__.__name__)
        try:
            try:
                visitor.dispatch_visit(self)
            except SkipNode:
                return stop
            except SkipDeparture:
                call_depart = False
            children = self.children
            try:
                for child in children[:]:
                    if child.walkabout(visitor):
                        stop = True
                        break
            except SkipSiblings:
                pass
        except SkipChildren:
            pass
        except StopTraversal:
            stop = True
        if call_depart:
            visitor.document.reporter.debug(
                'docutils.nodes.Node.walkabout calling dispatch_departure '
                'for %s' % self.__class__.__name__)
            visitor.dispatch_departure(self)
        return stop

    def _fast_findall(self, cls):
        """Return iterator that only supports instance checks."""
        if isinstance(self, cls):
            yield self
        for child in self.children:
            yield from child._fast_findall(cls)

    def _superfast_findall(self):
        """Return iterator that doesn't check for a condition."""
        # This is different from ``iter(self)`` implemented via
        # __getitem__() and __len__() in the Element subclass,
        # which yields only the direct children.
        yield self
        for child in self.children:
            yield from child._superfast_findall()

    def findall(self, condition=None, include_self=True, descend=True,
                siblings=False, ascend=False):
        """
        Return an iterator yielding nodes following `self`:

        * self (if `include_self` is true)
        * all descendants in tree traversal order (if `descend` is true)
        * the following siblings (if `siblings` is true) and their
          descendants (if also `descend` is true)
        * the following siblings of the parent (if `ascend` is true) and
          their descendants (if also `descend` is true), and so on.

        If `condition` is not None, the iterator yields only nodes
        for which ``condition(node)`` is true.  If `condition` is a
        node class ``cls``, it is equivalent to a function consisting
        of ``return isinstance(node, cls)``.

        If `ascend` is true, assume `siblings` to be true as well.

        If the tree structure is modified during iteration, the result
        is undefined.

        For example, given the following tree::

            <paragraph>
                <emphasis>      <--- emphasis.traverse() and
                    <strong>    <--- strong.traverse() are called.
                        Foo
                    Bar
                <reference name="Baz" refid="baz">
                    Baz

        Then tuple(emphasis.traverse()) equals ::

            (<emphasis>, <strong>, <#text: Foo>, <#text: Bar>)

        and list(strong.traverse(ascend=True) equals ::

            [<strong>, <#text: Foo>, <#text: Bar>, <reference>, <#text: Baz>]
        """
        if ascend:
            siblings = True
        # Check for special argument combinations that allow using an
        # optimized version of traverse()
        if include_self and descend and not siblings:
            if condition is None:
                yield from self._superfast_findall()
                return
            elif isinstance(condition, type):
                yield from self._fast_findall(condition)
                return
        # Check if `condition` is a class (check for TypeType for Python
        # implementations that use only new-style classes, like PyPy).
        if isinstance(condition, type):
            node_class = condition

            def condition(node, node_class=node_class):
                return isinstance(node, node_class)

        if include_self and (condition is None or condition(self)):
            yield self
        if descend and len(self.children):
            for child in self:
                yield from child.findall(condition=condition,
                                         include_self=True, descend=True,
                                         siblings=False, ascend=False)
        if siblings or ascend:
            node = self
            while node.parent:
                index = node.parent.index(node)
                # extra check since Text nodes have value-equality
                while node.parent[index] is not node:
                    index = node.parent.index(node, index + 1)
                for sibling in node.parent[index+1:]:
                    yield from sibling.findall(
                        condition=condition,
                        include_self=True, descend=descend,
                        siblings=False, ascend=False)
                if not ascend:
                    break
                else:
                    node = node.parent

    def traverse(self, condition=None, include_self=True, descend=True,
                 siblings=False, ascend=False):
        """Return list of nodes following `self`.

        For looping, Node.findall() is faster and more memory efficient.
        """
        # traverse() may be eventually removed:
        warnings.warn('nodes.Node.traverse() is obsoleted by Node.findall().',
                      PendingDeprecationWarning, stacklevel=2)
        return list(self.findall(condition, include_self, descend,
                                 siblings, ascend))

    def next_node(self, condition=None, include_self=False, descend=True,
                  siblings=False, ascend=False):
        """
        Return the first node in the iterator returned by findall(),
        or None if the iterable is empty.

        Parameter list is the same as of `findall()`.  Note that `include_self`
        defaults to False, though.
        """
        try:
            return next(self.findall(condition, include_self,
                                     descend, siblings, ascend))
        except StopIteration:
            return None


class Text(Node, str):
    """
    Instances are terminal nodes (leaves) containing text only; no child
    nodes or attributes.  Initialize by passing a string to the constructor.

    Access the raw (null-escaped) text with ``str(<instance>)``
    and unescaped text with ``<instance>.astext()``.
    """

    tagname = '#text'

    children = ()
    """Text nodes have no children, and cannot have children."""

    def __new__(cls, data, rawsource=None):
        """Assert that `data` is not an array of bytes
        and warn if the deprecated `rawsource` argument is used.
        """
        if isinstance(data, bytes):
            raise TypeError('expecting str data, not bytes')
        if rawsource is not None:
            warnings.warn('nodes.Text: initialization argument "rawsource" '
                          'is ignored and will be removed in Docutils 2.0.',
                          DeprecationWarning, stacklevel=2)
        return str.__new__(cls, data)

    def shortrepr(self, maxlen=18):
        data = self
        if len(data) > maxlen:
            data = data[:maxlen-4] + ' ...'
        return '<%s: %r>' % (self.tagname, str(data))

    def __repr__(self):
        return self.shortrepr(maxlen=68)

    def astext(self):
        return str(unescape(self))

    def _dom_node(self, domroot):
        return domroot.createTextNode(str(self))

    def copy(self):
        return self.__class__(str(self))

    def deepcopy(self):
        return self.copy()

    def pformat(self, indent='    ', level=0):
        try:
            if self.document.settings.detailed:
                tag = '%s%s' % (indent*level, '<#text>')
                lines = (indent*(level+1) + repr(line)
                         for line in self.splitlines(True))
                return '\n'.join((tag, *lines)) + '\n'
        except AttributeError:
            pass
        indent = indent * level
        lines = [indent+line for line in self.astext().splitlines()]
        if not lines:
            return ''
        return '\n'.join(lines) + '\n'

    # rstrip and lstrip are used by substitution definitions where
    # they are expected to return a Text instance, this was formerly
    # taken care of by UserString.

    def rstrip(self, chars=None):
        return self.__class__(str.rstrip(self, chars))

    def lstrip(self, chars=None):
        return self.__class__(str.lstrip(self, chars))

    def validate(self, recursive=True):
        """Validate Docutils Document Tree element ("doctree")."""
        # Text nodes have no attributes and no children.

    def check_position(self):
        """Hook for additional checks of the parent's content model."""
        # no special placement requirements for Text nodes


class Element(Node):
    """
    `Element` is the superclass to all specific elements.

    Elements contain attributes and child nodes.
    They can be described as a cross between a list and a dictionary.

    Elements emulate dictionaries for external [#]_ attributes, indexing by
    attribute name (a string). To set the attribute 'att' to 'value', do::

        element['att'] = 'value'

    .. [#] External attributes correspond to the XML element attributes.
       From its `Node` superclass, Element also inherits "internal"
       class attributes that are accessed using the standard syntax, e.g.
       ``element.parent``.

    There are two special attributes: 'ids' and 'names'.  Both are
    lists of unique identifiers: 'ids' conform to the regular expression
    ``[a-z](-?[a-z0-9]+)*`` (see the make_id() function for rationale and
    details). 'names' serve as user-friendly interfaces to IDs; they are
    case- and whitespace-normalized (see the fully_normalize_name() function).

    Elements emulate lists for child nodes (element nodes and/or text
    nodes), indexing by integer.  To get the first child node, use::

        element[0]

    to iterate over the child nodes (without descending), use::

        for child in element:
            ...

    Elements may be constructed using the ``+=`` operator.  To add one new
    child node to element, do::

        element += node

    This is equivalent to ``element.append(node)``.

    To add a list of multiple child nodes at once, use the same ``+=``
    operator::

        element += [node1, node2]

    This is equivalent to ``element.extend([node1, node2])``.
    """

    list_attributes = ('ids', 'classes', 'names', 'dupnames')
    """Tuple of attributes that are initialized to empty lists.

    NOTE: Derived classes should update this value when supporting
          additional list attributes.
    """

    valid_attributes = list_attributes + ('source',)
    """Tuple of attributes that are valid for elements of this class.

    NOTE: Derived classes should update this value when supporting
          additional attributes.
    """

    common_attributes = valid_attributes
    """Tuple of `common attributes`__  known to all Doctree Element classes.

    __ https://docutils.sourceforge.io/docs/ref/doctree.html#common-attributes
    """

    known_attributes = common_attributes
    """Alias for `common_attributes`. Will be removed in Docutils 2.0."""

    basic_attributes = list_attributes
    """Common list attributes. Deprecated. Will be removed in Docutils 2.0."""

    local_attributes = ('backrefs',)
    """Obsolete. Will be removed in Docutils 2.0."""

    content_model = tuple()
    """Python representation of the element's content model (cf. docutils.dtd).

    A tuple of ``(category, quantifier)`` tuples with

    :category:   class or tuple of classes that are expected at this place(s)
                 in the list of children
    :quantifier: string representation stating how many elements
                 of `category` are expected. Value is one of:
                 '.' (exactly one), '?' (zero or one),
                 '+' (one or more), '*' (zero or more).

    NOTE: The default describes the empty element. Derived classes should
    update this value to match teir content model.

    Provisional.
    """

    tagname = None
    """The element generic identifier.

    If None, it is set as an instance attribute to the name of the class.
    """

    child_text_separator = '\n\n'
    """Separator for child nodes, used by `astext()` method."""

    def __init__(self, rawsource='', *children, **attributes):
        self.rawsource = rawsource
        """The raw text from which this element was constructed.

        For informative and debugging purposes. Don't rely on its value!

        NOTE: some elements do not set this value (default '').
        """
        if isinstance(rawsource, Element):
            raise TypeError('First argument "rawsource" must be a string.')

        self.children = []
        """List of child nodes (elements and/or `Text`)."""

        self.extend(children)           # maintain parent info

        self.attributes = {}
        """Dictionary of attribute {name: value}."""

        # Initialize list attributes.
        for att in self.list_attributes:
            self.attributes[att] = []

        for att, value in attributes.items():
            att = att.lower()  # normalize attribute name
            if att in self.list_attributes:
                # lists are mutable; make a copy for this node
                self.attributes[att] = value[:]
            else:
                self.attributes[att] = value

        if self.tagname is None:
            self.tagname = self.__class__.__name__

    def _dom_node(self, domroot):
        element = domroot.createElement(self.tagname)
        for attribute, value in self.attlist():
            if isinstance(value, list):
                value = ' '.join(serial_escape('%s' % (v,)) for v in value)
            element.setAttribute(attribute, '%s' % value)
        for child in self.children:
            element.appendChild(child._dom_node(domroot))
        return element

    def __repr__(self):
        data = ''
        for c in self.children:
            data += c.shortrepr()
            if len(data) > 60:
                data = data[:56] + ' ...'
                break
        if self['names']:
            return '<%s "%s": %s>' % (self.__class__.__name__,
                                      '; '.join(self['names']), data)
        else:
            return '<%s: %s>' % (self.__class__.__name__, data)

    def shortrepr(self):
        if self['names']:
            return '<%s "%s"...>' % (self.__class__.__name__,
                                     '; '.join(self['names']))
        else:
            return '<%s...>' % self.tagname

    def __str__(self):
        if self.children:
            return '%s%s%s' % (self.starttag(),
                               ''.join(str(c) for c in self.children),
                               self.endtag())
        else:
            return self.emptytag()

    def starttag(self, quoteattr=None):
        # the optional arg is used by the docutils_xml writer
        if quoteattr is None:
            quoteattr = pseudo_quoteattr
        parts = [self.tagname]
        for name, value in self.attlist():
            if value is None:           # boolean attribute
                parts.append('%s="True"' % name)
                continue
            if isinstance(value, bool):
                value = str(int(value))
            if isinstance(value, list):
                values = [serial_escape('%s' % (v,)) for v in value]
                value = ' '.join(values)
            else:
                value = str(value)
            value = quoteattr(value)
            parts.append('%s=%s' % (name, value))
        return '<%s>' % ' '.join(parts)

    def endtag(self):
        return '</%s>' % self.tagname

    def emptytag(self):
        attributes = ('%s="%s"' % (n, v) for n, v in self.attlist())
        return '<%s/>' % ' '.join((self.tagname, *attributes))

    def __len__(self):
        return len(self.children)

    def __contains__(self, key):
        # Test for both, children and attributes with operator ``in``.
        if isinstance(key, str):
            return key in self.attributes
        return key in self.children

    def __getitem__(self, key):
        if isinstance(key, str):
            return self.attributes[key]
        elif isinstance(key, int):
            return self.children[key]
        elif isinstance(key, slice):
            assert key.step in (None, 1), 'cannot handle slice with stride'
            return self.children[key.start:key.stop]
        else:
            raise TypeError('element index must be an integer, a slice, or '
                            'an attribute name string')

    def __setitem__(self, key, item):
        if isinstance(key, str):
            self.attributes[str(key)] = item
        elif isinstance(key, int):
            self.setup_child(item)
            self.children[key] = item
        elif isinstance(key, slice):
            assert key.step in (None, 1), 'cannot handle slice with stride'
            for node in item:
                self.setup_child(node)
            self.children[key.start:key.stop] = item
        else:
            raise TypeError('element index must be an integer, a slice, or '
                            'an attribute name string')

    def __delitem__(self, key):
        if isinstance(key, str):
            del self.attributes[key]
        elif isinstance(key, int):
            del self.children[key]
        elif isinstance(key, slice):
            assert key.step in (None, 1), 'cannot handle slice with stride'
            del self.children[key.start:key.stop]
        else:
            raise TypeError('element index must be an integer, a simple '
                            'slice, or an attribute name string')

    def __add__(self, other):
        return self.children + other

    def __radd__(self, other):
        return other + self.children

    def __iadd__(self, other):
        """Append a node or a list of nodes to `self.children`."""
        if isinstance(other, Node):
            self.append(other)
        elif other is not None:
            self.extend(other)
        return self

    def astext(self):
        return self.child_text_separator.join(
                   [child.astext() for child in self.children])

    def non_default_attributes(self):
        atts = {}
        for key, value in self.attributes.items():
            if self.is_not_default(key):
                atts[key] = value
        return atts

    def attlist(self):
        return sorted(self.non_default_attributes().items())

    def get(self, key, failobj=None):
        return self.attributes.get(key, failobj)

    def hasattr(self, attr):
        return attr in self.attributes

    def delattr(self, attr):
        if attr in self.attributes:
            del self.attributes[attr]

    def setdefault(self, key, failobj=None):
        return self.attributes.setdefault(key, failobj)

    has_key = hasattr

    def get_language_code(self, fallback=''):
        """Return node's language tag.

        Look iteratively in self and parents for a class argument
        starting with ``language-`` and return the remainder of it
        (which should be a `BCP49` language tag) or the `fallback`.
        """
        for cls in self.get('classes', []):
            if cls.startswith('language-'):
                return cls[9:]
        try:
            return self.parent.get_language(fallback)
        except AttributeError:
            return fallback

    def append(self, item):
        self.setup_child(item)
        self.children.append(item)

    def extend(self, item):
        for node in item:
            self.append(node)

    def insert(self, index, item):
        if isinstance(item, Node):
            self.setup_child(item)
            self.children.insert(index, item)
        elif item is not None:
            self[index:index] = item

    def pop(self, i=-1):
        return self.children.pop(i)

    def remove(self, item):
        self.children.remove(item)

    def index(self, item, start=0, stop=sys.maxsize):
        return self.children.index(item, start, stop)

    def previous_sibling(self):
        """Return preceding sibling node or ``None``."""
        try:
            i = self.parent.index(self)
        except (AttributeError):
            return None
        return self.parent[i-1] if i > 0 else None

    def is_not_default(self, key):
        if self[key] == [] and key in self.list_attributes:
            return 0
        else:
            return 1

    def update_basic_atts(self, dict_):
        """
        Update basic attributes ('ids', 'names', 'classes',
        'dupnames', but not 'source') from node or dictionary `dict_`.

        Provisional.
        """
        if isinstance(dict_, Node):
            dict_ = dict_.attributes
        for att in self.basic_attributes:
            self.append_attr_list(att, dict_.get(att, []))

    def append_attr_list(self, attr, values):
        """
        For each element in values, if it does not exist in self[attr], append
        it.

        NOTE: Requires self[attr] and values to be sequence type and the
        former should specifically be a list.
        """
        # List Concatenation
        for value in values:
            if value not in self[attr]:
                self[attr].append(value)

    def coerce_append_attr_list(self, attr, value):
        """
        First, convert both self[attr] and value to a non-string sequence
        type; if either is not already a sequence, convert it to a list of one
        element.  Then call append_attr_list.

        NOTE: self[attr] and value both must not be None.
        """
        # List Concatenation
        if not isinstance(self.get(attr), list):
            self[attr] = [self[attr]]
        if not isinstance(value, list):
            value = [value]
        self.append_attr_list(attr, value)

    def replace_attr(self, attr, value, force=True):
        """
        If self[attr] does not exist or force is True or omitted, set
        self[attr] to value, otherwise do nothing.
        """
        # One or the other
        if force or self.get(attr) is None:
            self[attr] = value

    def copy_attr_convert(self, attr, value, replace=True):
        """
        If attr is an attribute of self, set self[attr] to
        [self[attr], value], otherwise set self[attr] to value.

        NOTE: replace is not used by this function and is kept only for
              compatibility with the other copy functions.
        """
        if self.get(attr) is not value:
            self.coerce_append_attr_list(attr, value)

    def copy_attr_coerce(self, attr, value, replace):
        """
        If attr is an attribute of self and either self[attr] or value is a
        list, convert all non-sequence values to a sequence of 1 element and
        then concatenate the two sequence, setting the result to self[attr].
        If both self[attr] and value are non-sequences and replace is True or
        self[attr] is None, replace self[attr] with value. Otherwise, do
        nothing.
        """
        if self.get(attr) is not value:
            if isinstance(self.get(attr), list) or \
               isinstance(value, list):
                self.coerce_append_attr_list(attr, value)
            else:
                self.replace_attr(attr, value, replace)

    def copy_attr_concatenate(self, attr, value, replace):
        """
        If attr is an attribute of self and both self[attr] and value are
        lists, concatenate the two sequences, setting the result to
        self[attr].  If either self[attr] or value are non-sequences and
        replace is True or self[attr] is None, replace self[attr] with value.
        Otherwise, do nothing.
        """
        if self.get(attr) is not value:
            if isinstance(self.get(attr), list) and \
               isinstance(value, list):
                self.append_attr_list(attr, value)
            else:
                self.replace_attr(attr, value, replace)

    def copy_attr_consistent(self, attr, value, replace):
        """
        If replace is True or self[attr] is None, replace self[attr] with
        value.  Otherwise, do nothing.
        """
        if self.get(attr) is not value:
            self.replace_attr(attr, value, replace)

    def update_all_atts(self, dict_, update_fun=copy_attr_consistent,
                        replace=True, and_source=False):
        """
        Updates all attributes from node or dictionary `dict_`.

        Appends the basic attributes ('ids', 'names', 'classes',
        'dupnames', but not 'source') and then, for all other attributes in
        dict_, updates the same attribute in self.  When attributes with the
        same identifier appear in both self and dict_, the two values are
        merged based on the value of update_fun.  Generally, when replace is
        True, the values in self are replaced or merged with the values in
        dict_; otherwise, the values in self may be preserved or merged.  When
        and_source is True, the 'source' attribute is included in the copy.

        NOTE: When replace is False, and self contains a 'source' attribute,
              'source' is not replaced even when dict_ has a 'source'
              attribute, though it may still be merged into a list depending
              on the value of update_fun.
        NOTE: It is easier to call the update-specific methods then to pass
              the update_fun method to this function.
        """
        if isinstance(dict_, Node):
            dict_ = dict_.attributes

        # Include the source attribute when copying?
        if and_source:
            filter_fun = self.is_not_list_attribute
        else:
            filter_fun = self.is_not_known_attribute

        # Copy the basic attributes
        self.update_basic_atts(dict_)

        # Grab other attributes in dict_ not in self except the
        # (All basic attributes should be copied already)
        for att in filter(filter_fun, dict_):
            update_fun(self, att, dict_[att], replace)

    def update_all_atts_consistantly(self, dict_, replace=True,
                                     and_source=False):
        """
        Updates all attributes from node or dictionary `dict_`.

        Appends the basic attributes ('ids', 'names', 'classes',
        'dupnames', but not 'source') and then, for all other attributes in
        dict_, updates the same attribute in self.  When attributes with the
        same identifier appear in both self and dict_ and replace is True, the
        values in self are replaced with the values in dict_; otherwise, the
        values in self are preserved.  When and_source is True, the 'source'
        attribute is included in the copy.

        NOTE: When replace is False, and self contains a 'source' attribute,
              'source' is not replaced even when dict_ has a 'source'
              attribute, though it may still be merged into a list depending
              on the value of update_fun.
        """
        self.update_all_atts(dict_, Element.copy_attr_consistent, replace,
                             and_source)

    def update_all_atts_concatenating(self, dict_, replace=True,
                                      and_source=False):
        """
        Updates all attributes from node or dictionary `dict_`.

        Appends the basic attributes ('ids', 'names', 'classes',
        'dupnames', but not 'source') and then, for all other attributes in
        dict_, updates the same attribute in self.  When attributes with the
        same identifier appear in both self and dict_ whose values aren't each
        lists and replace is True, the values in self are replaced with the
        values in dict_; if the values from self and dict_ for the given
        identifier are both of list type, then the two lists are concatenated
        and the result stored in self; otherwise, the values in self are
        preserved.  When and_source is True, the 'source' attribute is
        included in the copy.

        NOTE: When replace is False, and self contains a 'source' attribute,
              'source' is not replaced even when dict_ has a 'source'
              attribute, though it may still be merged into a list depending
              on the value of update_fun.
        """
        self.update_all_atts(dict_, Element.copy_attr_concatenate, replace,
                             and_source)

    def update_all_atts_coercion(self, dict_, replace=True,
                                 and_source=False):
        """
        Updates all attributes from node or dictionary `dict_`.

        Appends the basic attributes ('ids', 'names', 'classes',
        'dupnames', but not 'source') and then, for all other attributes in
        dict_, updates the same attribute in self.  When attributes with the
        same identifier appear in both self and dict_ whose values are both
        not lists and replace is True, the values in self are replaced with
        the values in dict_; if either of the values from self and dict_ for
        the given identifier are of list type, then first any non-lists are
        converted to 1-element lists and then the two lists are concatenated
        and the result stored in self; otherwise, the values in self are
        preserved.  When and_source is True, the 'source' attribute is
        included in the copy.

        NOTE: When replace is False, and self contains a 'source' attribute,
              'source' is not replaced even when dict_ has a 'source'
              attribute, though it may still be merged into a list depending
              on the value of update_fun.
        """
        self.update_all_atts(dict_, Element.copy_attr_coerce, replace,
                             and_source)

    def update_all_atts_convert(self, dict_, and_source=False):
        """
        Updates all attributes from node or dictionary `dict_`.

        Appends the basic attributes ('ids', 'names', 'classes',
        'dupnames', but not 'source') and then, for all other attributes in
        dict_, updates the same attribute in self.  When attributes with the
        same identifier appear in both self and dict_ then first any non-lists
        are converted to 1-element lists and then the two lists are
        concatenated and the result stored in self; otherwise, the values in
        self are preserved.  When and_source is True, the 'source' attribute
        is included in the copy.

        NOTE: When replace is False, and self contains a 'source' attribute,
              'source' is not replaced even when dict_ has a 'source'
              attribute, though it may still be merged into a list depending
              on the value of update_fun.
        """
        self.update_all_atts(dict_, Element.copy_attr_convert,
                             and_source=and_source)

    def clear(self):
        self.children = []

    def replace(self, old, new):
        """Replace one child `Node` with another child or children."""
        index = self.index(old)
        if isinstance(new, Node):
            self.setup_child(new)
            self[index] = new
        elif new is not None:
            self[index:index+1] = new

    def replace_self(self, new):
        """
        Replace `self` node with `new`, where `new` is a node or a
        list of nodes.

        Provisional: the handling of node attributes will be revised.
        """
        update = new
        if not isinstance(new, Node):
            # `new` is a list; update first child.
            try:
                update = new[0]
            except IndexError:
                update = None
        if isinstance(update, Element):
            update.update_basic_atts(self)
        else:
            # `update` is a Text node or `new` is an empty list.
            # Assert that we aren't losing any attributes.
            for att in self.basic_attributes:
                assert not self[att], \
                       'Losing "%s" attribute: %s' % (att, self[att])
        self.parent.replace(self, new)

    def first_child_matching_class(self, childclass, start=0, end=sys.maxsize):
        """
        Return the index of the first child whose class exactly matches.

        Parameters:

        - `childclass`: A `Node` subclass to search for, or a tuple of `Node`
          classes. If a tuple, any of the classes may match.
        - `start`: Initial index to check.
        - `end`: Initial index to *not* check.
        """
        if not isinstance(childclass, tuple):
            childclass = (childclass,)
        for index in range(start, min(len(self), end)):
            for c in childclass:
                if isinstance(self[index], c):
                    return index
        return None

    def first_child_not_matching_class(self, childclass, start=0,
                                       end=sys.maxsize):
        """
        Return the index of the first child whose class does *not* match.

        Parameters:

        - `childclass`: A `Node` subclass to skip, or a tuple of `Node`
          classes. If a tuple, none of the classes may match.
        - `start`: Initial index to check.
        - `end`: Initial index to *not* check.
        """
        if not isinstance(childclass, tuple):
            childclass = (childclass,)
        for index in range(start, min(len(self), end)):
            for c in childclass:
                if isinstance(self.children[index], c):
                    break
            else:
                return index
        return None

    def pformat(self, indent='    ', level=0):
        tagline = '%s%s\n' % (indent*level, self.starttag())
        childreps = (c.pformat(indent, level+1) for c in self.children)
        return ''.join((tagline, *childreps))

    def copy(self):
        obj = self.__class__(rawsource=self.rawsource, **self.attributes)
        obj._document = self._document
        obj.source = self.source
        obj.line = self.line
        return obj

    def deepcopy(self):
        copy = self.copy()
        copy.extend([child.deepcopy() for child in self.children])
        return copy

    def set_class(self, name):
        """Add a new class to the "classes" attribute."""
        warnings.warn('docutils.nodes.Element.set_class() is deprecated; '
                      ' and will be removed in Docutils 0.21 or later.'
                      "Append to Element['classes'] list attribute directly",
                      DeprecationWarning, stacklevel=2)
        assert ' ' not in name
        self['classes'].append(name.lower())

    def note_referenced_by(self, name=None, id=None):
        """Note that this Element has been referenced by its name
        `name` or id `id`."""
        self.referenced = True
        # Element.expect_referenced_by_* dictionaries map names or ids
        # to nodes whose ``referenced`` attribute is set to true as
        # soon as this node is referenced by the given name or id.
        # Needed for target propagation.
        by_name = getattr(self, 'expect_referenced_by_name', {}).get(name)
        by_id = getattr(self, 'expect_referenced_by_id', {}).get(id)
        if by_name:
            assert name is not None
            by_name.referenced = True
        if by_id:
            assert id is not None
            by_id.referenced = True

    @classmethod
    def is_not_list_attribute(cls, attr):
        """
        Returns True if and only if the given attribute is NOT one of the
        basic list attributes defined for all Elements.
        """
        return attr not in cls.list_attributes

    @classmethod
    def is_not_known_attribute(cls, attr):
        """
        Return True if `attr` is NOT defined for all Element instances.

        Provisional. May be removed in Docutils 2.0.
        """
        return attr not in cls.common_attributes

    def validate_attributes(self):
        """Normalize and validate element attributes.

        Convert string values to expected datatype.
        Normalize values.

        Raise `ValidationError` for invalid attributes or attribute values.

        Provisional.
        """
        messages = []
        for key, value in self.attributes.items():
            if key.startswith('internal:'):
                continue  # see docs/user/config.html#expose-internals
            if key not in self.valid_attributes:
                va = '", "'.join(self.valid_attributes)
                messages.append(f'Attribute "{key}" not one of "{va}".')
                continue
            try:
                self.attributes[key] = ATTRIBUTE_VALIDATORS[key](value)
            except (ValueError, TypeError, KeyError) as e:
                messages.append(
                    f'Attribute "{key}" has invalid value "{value}".\n  {e}')
        if messages:
            raise ValidationError(f'Element {self.starttag()} invalid:\n  '
                                  + '\n  '.join(messages),
                                  problematic_element=self)

    def validate_content(self, model=None, elements=None):
        """Test compliance of `elements` with `model`.

        :model: content model description, default `self.content_model`,
        :elements: list of doctree elements, default `self.children`.

        Return list of children that do not fit in the model or raise
        `ValidationError` if the content does not comply with the `model`.

        Provisional.
        """
        if model is None:
            model = self.content_model
        if elements is None:
            elements = self.children
        ichildren = iter(elements)
        child = next(ichildren, None)
        for category, quantifier in model:
            if not isinstance(child, category):
                if quantifier in ('.', '+'):
                    raise ValidationError(self._report_child(child, category),
                                          problematic_element=child)
                else:  # quantifier in ('?', '*') -> optional child
                    continue  # try same child with next part of content model
            else:
                # Check additional placement constraints (if applicable):
                child.check_position()
            # advance:
            if quantifier in ('.', '?'):  # go to next element
                child = next(ichildren, None)
            else:  # if quantifier in ('*', '+'):  # pass all matching elements
                for child in ichildren:
                    if not isinstance(child, category):
                        break
                    child.check_position()
                else:
                    child = None
        return [] if child is None else [child, *ichildren]

    def _report_child(self, child, category):
        # Return a str reporting a missing child or child of wrong category.
        try:
            type = category.__name__
        except AttributeError:
            type = '> or <'.join(c.__name__ for c in category)
        msg = f'Element {self.starttag()} invalid:\n'
        if child is None:
            return f'{msg}  Missing child of type <{type}>.'
        if isinstance(child, Text):
            return (f'{msg}  Expecting child of type <{type}>, '
                    f'not text data "{child.astext()}".')
        return (f'{msg}  Expecting child of type <{type}>, '
                f'not {child.starttag()}.')

    def check_position(self):
        """Hook for additional checks of the parent's content model.

        Raise ValidationError, if `self` is at an invalid position.
        See `subtitle.check_position()` and `transition.check_position()`.
        """

    def validate(self, recursive=True):
        """Validate Docutils Document Tree element ("doctree").

        Raise ValidationError if there are violations.
        If `recursive` is True, validate also the element's descendants.

        See `The Docutils Document Tree`__ for details of the
        Docutils Document Model.

        __ https://docutils.sourceforge.io/docs/ref/doctree.html

        Provisional (work in progress).
        """
        self.validate_attributes()

        leftover_childs = self.validate_content()
        for child in leftover_childs:
            if isinstance(child, Text):
                raise ValidationError(f'Element {self.starttag()} invalid:\n'
                                      f'  Spurious text: "{child.astext()}".',
                                      problematic_element=self)
            else:
                raise ValidationError(f'Element {self.starttag()} invalid:\n'
                                      f'  Child element {child.starttag()} '
                                      'not allowed at this position.',
                                      problematic_element=child)

        if recursive:
            for child in self:
                child.validate(recursive=recursive)


# ====================
#  Element Categories
# ====================
#
# See https://docutils.sourceforge.io/docs/ref/doctree.html#element-hierarchy.

class Root:
    """Element at the root of a document tree."""


class Structural:
    """`Structural elements`__.

    __ https://docutils.sourceforge.io/docs/ref/doctree.html
       #structural-elements
    """


class SubStructural:
    """`Structural subelements`__ are children of `Structural` elements.

    Most Structural elements accept only specific `SubStructural` elements.

    __ https://docutils.sourceforge.io/docs/ref/doctree.html
       #structural-subelements
    """


class Bibliographic:
    """`Bibliographic Elements`__ (displayed document meta-data).

    __ https://docutils.sourceforge.io/docs/ref/doctree.html
       #bibliographic-elements
    """


class Body:
    """`Body elements`__.

    __ https://docutils.sourceforge.io/docs/ref/doctree.html#body-elements
    """


class Admonition(Body):
    """Admonitions (distinctive and self-contained notices)."""
    content_model = ((Body, '+'),)  # (%body.elements;)+


class Sequential(Body):
    """List-like body elements."""


class General(Body):
    """Miscellaneous body elements."""


class Special(Body):
    """Special internal body elements."""


class Part:
    """`Body Subelements`__ always occur within specific parent elements.

    __ https://docutils.sourceforge.io/docs/ref/doctree.html#body-subelements
    """


class Decorative:
    """Decorative elements (`header` and `footer`).

    Children of `decoration`.
    """
    content_model = ((Body, '+'),)  # (%body.elements;)+


class Inline:
    """Inline elements contain text data and possibly other inline elements.
    """


# Orthogonal categories and Mixins
# ================================

class PreBibliographic:
    """Elements which may occur before Bibliographic Elements."""


class Invisible(Special, PreBibliographic):
    """Internal elements that don't appear in output."""


class Labeled:
    """Contains a `label` as its first element."""


class Resolvable:
    resolved = False


class BackLinkable:
    """Mixin for Elements that accept a "backrefs" attribute."""

    list_attributes = Element.list_attributes + ('backrefs',)
    valid_attributes = Element.valid_attributes + ('backrefs',)

    def add_backref(self, refid):
        self['backrefs'].append(refid)


class Referential(Resolvable):
    """Elements holding a cross-reference (outgoing hyperlink)."""


class Targetable(Resolvable):
    """Cross-reference targets (incoming hyperlink)."""
    referenced = 0

    indirect_reference_name = None
    """Holds the whitespace_normalized_name (contains mixed case) of a target.
    Required for MoinMoin/reST compatibility.

    Provisional.
    """


class Titular:
    """Title, sub-title, or informal heading (rubric)."""


class TextElement(Element):
    """
    An element which directly contains text.

    Its children are all `Text` or `Inline` subclass nodes.  You can
    check whether an element's context is inline simply by checking whether
    its immediate parent is a `TextElement` instance (including subclasses).
    This is handy for nodes like `image` that can appear both inline and as
    standalone body elements.

    If passing children to `__init__()`, make sure to set `text` to
    ``''`` or some other suitable value.
    """
    content_model = (  # (#PCDATA | %inline.elements;)*
                     ((Text, Inline), '*'),)

    child_text_separator = ''
    """Separator for child nodes, used by `astext()` method."""

    def __init__(self, rawsource='', text='', *children, **attributes):
        if text:
            textnode = Text(text)
            Element.__init__(self, rawsource, textnode, *children,
                             **attributes)
        else:
            Element.__init__(self, rawsource, *children, **attributes)


class FixedTextElement(TextElement):
    """An element which directly contains preformatted text."""

    valid_attributes = Element.valid_attributes + ('xml:space',)

    def __init__(self, rawsource='', text='', *children, **attributes):
        super().__init__(rawsource, text, *children, **attributes)
        self.attributes['xml:space'] = 'preserve'


class PureTextElement(TextElement):
    """An element which only contains text, no children."""
    content_model = ((Text, '?'),)  # (#PCDATA)


# =================================
#  Concrete Document Tree Elements
# =================================
#
# See https://docutils.sourceforge.io/docs/ref/doctree.html#element-reference

# Decorative Elements
# ===================

class header(Decorative, Element): pass
class footer(Decorative, Element): pass


# Structural Subelements
# ======================

class title(Titular, PreBibliographic, SubStructural, TextElement):
    """Title of `document`, `section`, `topic` and generic `admonition`.
    """
    valid_attributes = Element.valid_attributes + ('auto', 'refid')


class subtitle(Titular, PreBibliographic, SubStructural, TextElement):
    """Sub-title of `document`, `section` and `sidebar`."""

    def check_position(self):
        """Check position of subtitle: must follow a title."""
        if self.parent and self.parent.index(self) == 0:
            raise ValidationError(f'Element {self.parent.starttag()} invalid:'
                                  '\n  <subtitle> only allowed after <title>.',
                                  problematic_element=self)


class meta(PreBibliographic, SubStructural, Element):
    """Container for "invisible" bibliographic data, or meta-data."""
    valid_attributes = Element.valid_attributes + (
        'content', 'dir', 'http-equiv', 'lang', 'media', 'name', 'scheme')


class docinfo(SubStructural, Element):
    """Container for displayed document meta-data."""
    content_model = (  # (%bibliographic.elements;)+
                     (Bibliographic, '+'),)


class decoration(PreBibliographic, SubStructural, Element):
    """Container for `header` and `footer`."""
    content_model = (  # (header?, footer?)
                     (header, '?'),
                     (footer, '?'))  # TODO: empty element does not make sense.

    def get_header(self):
        if not len(self.children) or not isinstance(self.children[0], header):
            self.insert(0, header())
        return self.children[0]

    def get_footer(self):
        if not len(self.children) or not isinstance(self.children[-1], footer):
            self.append(footer())
        return self.children[-1]


class transition(SubStructural, Element):
    """Transitions__ are breaks between untitled text parts.

    __ https://docutils.sourceforge.io/docs/ref/doctree.html#transition
    """

    def check_position(self):
        """Check additional constraints on `transition` placement.

        A transition may not begin or end a section or document,
        nor may two transitions be immediately adjacent.
        """
        messages = [f'Element {self.parent.starttag()} invalid:']
        predecessor = self.previous_sibling()
        if (predecessor is None  # index == 0
            or isinstance(predecessor, (title, subtitle, meta, decoration))
            # A transition following these elements still counts as
            # "at the beginning of a document or section".
            ):
            messages.append(
                '<transition> may not begin a section or document.')
        if self.parent.index(self) == len(self.parent) - 1:
            messages.append('<transition> may not end a section or document.')
        if isinstance(predecessor, transition):
            messages.append(
                '<transition> may not directly follow another transition.')
        if len(messages) > 1:
            raise ValidationError('\n  '.join(messages),
                                  problematic_element=self)


# Structural Elements
# ===================

class topic(Structural, Element):
    """
    Topics__ are non-recursive, mini-sections.

    __ https://docutils.sourceforge.io/docs/ref/doctree.html#topic
    """
    content_model = (  # (title?, (%body.elements;)+)
                     (title, '?'),
                     (Body, '+'))


class sidebar(Structural, Element):
    """
    Sidebars__ are like parallel documents providing related material.

    A sidebar is typically offset by a border and "floats" to the side
    of the page

    __ https://docutils.sourceforge.io/docs/ref/doctree.html#sidebar
    """
    content_model = (  # ((title, subtitle?)?, (%body.elements; | topic)+)
                     (title, '?'),
                     (subtitle, '?'),
                     ((topic, Body), '+'))
    # "subtitle only after title" is ensured in `subtitle.check_position()`.


class section(Structural, Element):
    """Document section__. The main unit of hierarchy.

    __ https://docutils.sourceforge.io/docs/ref/doctree.html#section
    """
    # recursive content model, see below


section.content_model = (  # (title, subtitle?, %structure.model;)
                         (title, '.'),
                         (subtitle, '?'),
                         ((Body, topic, sidebar, transition), '*'),
                         ((section, transition), '*'),
                         )
# Correct transition placement is ensured in `transition.check_position()`.


# Root Element
# ============

class document(Root, Element):
    """
    The document root element.

    Do not instantiate this class directly; use
    `docutils.utils.new_document()` instead.
    """
    valid_attributes = Element.valid_attributes + ('title',)
    content_model = (  # ( (title, subtitle?)?,
                       #    meta*,
                       #    decoration?,
                       #    (docinfo, transition?)?,
                       #    %structure.model; )
                      (title, '?'),
                      (subtitle, '?'),
                      (meta, '*'),
                      (decoration, '?'),
                      (docinfo, '?'),
                      (transition, '?'),
                      ((Body, topic, sidebar, transition), '*'),
                      ((section, transition), '*'),
                     )
    # Additional restrictions for `subtitle` and `transition` are tested
    # with the respective `check_position()` methods.

    def __init__(self, settings, reporter, *args, **kwargs):
        Element.__init__(self, *args, **kwargs)

        self.current_source = None
        """Path to or description of the input source being processed."""

        self.current_line = None
        """Line number (1-based) of `current_source`."""

        self.settings = settings
        """Runtime settings data record."""

        self.reporter = reporter
        """System message generator."""

        self.indirect_targets = []
        """List of indirect target nodes."""

        self.substitution_defs = {}
        """Mapping of substitution names to substitution_definition nodes."""

        self.substitution_names = {}
        """Mapping of case-normalized substitution names to case-sensitive
        names."""

        self.refnames = {}
        """Mapping of names to lists of referencing nodes."""

        self.refids = {}
        """Mapping of ids to lists of referencing nodes."""

        self.nameids = {}
        """Mapping of names to unique id's."""

        self.nametypes = {}
        """Mapping of names to hyperlink type (boolean: True => explicit,
        False => implicit."""

        self.ids = {}
        """Mapping of ids to nodes."""

        self.footnote_refs = {}
        """Mapping of footnote labels to lists of footnote_reference nodes."""

        self.citation_refs = {}
        """Mapping of citation labels to lists of citation_reference nodes."""

        self.autofootnotes = []
        """List of auto-numbered footnote nodes."""

        self.autofootnote_refs = []
        """List of auto-numbered footnote_reference nodes."""

        self.symbol_footnotes = []
        """List of symbol footnote nodes."""

        self.symbol_footnote_refs = []
        """List of symbol footnote_reference nodes."""

        self.footnotes = []
        """List of manually-numbered footnote nodes."""

        self.citations = []
        """List of citation nodes."""

        self.autofootnote_start = 1
        """Initial auto-numbered footnote number."""

        self.symbol_footnote_start = 0
        """Initial symbol footnote symbol index."""

        self.id_counter = Counter()
        """Numbers added to otherwise identical IDs."""

        self.parse_messages = []
        """System messages generated while parsing."""

        self.transform_messages = []
        """System messages generated while applying transforms."""

        import docutils.transforms
        self.transformer = docutils.transforms.Transformer(self)
        """Storage for transforms to be applied to this document."""

        self.include_log = []
        """The current source's parents (to detect inclusion loops)."""

        self.decoration = None
        """Document's `decoration` node."""

        self._document = self

    def __getstate__(self):
        """
        Return dict with unpicklable references removed.
        """
        state = self.__dict__.copy()
        state['reporter'] = None
        state['transformer'] = None
        return state

    def asdom(self, dom=None):
        """Return a DOM representation of this document."""
        if dom is None:
            import xml.dom.minidom as dom
        domroot = dom.Document()
        domroot.appendChild(self._dom_node(domroot))
        return domroot

    def set_id(self, node, msgnode=None, suggested_prefix=''):
        if node['ids']:
            # register and check for duplicates
            for id in node['ids']:
                self.ids.setdefault(id, node)
                if self.ids[id] is not node:
                    msg = self.reporter.severe('Duplicate ID: "%s".' % id)
                    if msgnode is not None:
                        msgnode += msg
            return id
        # generate and set id
        id_prefix = self.settings.id_prefix
        auto_id_prefix = self.settings.auto_id_prefix
        base_id = ''
        id = ''
        for name in node['names']:
            if id_prefix:
                # allow names starting with numbers if `id_prefix`
                base_id = make_id('x'+name)[1:]
            else:
                base_id = make_id(name)
            # TODO: normalize id-prefix? (would make code simpler)
            id = id_prefix + base_id
            if base_id and id not in self.ids:
                break
        else:
            if base_id and auto_id_prefix.endswith('%'):
                # disambiguate name-derived ID
                # TODO: remove second condition after announcing change
                prefix = id + '-'
            else:
                prefix = id_prefix + auto_id_prefix
                if prefix.endswith('%'):
                    prefix = '%s%s-' % (prefix[:-1],
                                        suggested_prefix
                                        or make_id(node.tagname))
            while True:
                self.id_counter[prefix] += 1
                id = '%s%d' % (prefix, self.id_counter[prefix])
                if id not in self.ids:
                    break
        node['ids'].append(id)
        self.ids[id] = node
        return id

    def set_name_id_map(self, node, id, msgnode=None, explicit=None):
        """
        `self.nameids` maps names to IDs, while `self.nametypes` maps names to
        booleans representing hyperlink type (True==explicit,
        False==implicit).  This method updates the mappings.

        The following state transition table shows how `self.nameids` items
        ("id") and `self.nametypes` items ("type") change with new input
        (a call to this method), and what actions are performed
        ("implicit"-type system messages are INFO/1, and
        "explicit"-type system messages are ERROR/3):

        ====  =====  ========  ========  =======  ====  =====  =====
         Old State    Input          Action        New State   Notes
        -----------  --------  -----------------  -----------  -----
        id    type   new type  sys.msg.  dupname  id    type
        ====  =====  ========  ========  =======  ====  =====  =====
        -     -      explicit  -         -        new   True
        -     -      implicit  -         -        new   False
        -     False  explicit  -         -        new   True
        old   False  explicit  implicit  old      new   True
        -     True   explicit  explicit  new      -     True
        old   True   explicit  explicit  new,old  -     True   [#]_
        -     False  implicit  implicit  new      -     False
        old   False  implicit  implicit  new,old  -     False
        -     True   implicit  implicit  new      -     True
        old   True   implicit  implicit  new      old   True
        ====  =====  ========  ========  =======  ====  =====  =====

        .. [#] Do not clear the name-to-id map or invalidate the old target if
           both old and new targets are external and refer to identical URIs.
           The new target is invalidated regardless.
        """
        for name in tuple(node['names']):
            if name in self.nameids:
                self.set_duplicate_name_id(node, id, name, msgnode, explicit)
                # attention: modifies node['names']
            else:
                self.nameids[name] = id
                self.nametypes[name] = explicit

    def set_duplicate_name_id(self, node, id, name, msgnode, explicit):
        old_id = self.nameids[name]
        old_explicit = self.nametypes[name]
        self.nametypes[name] = old_explicit or explicit
        if explicit:
            if old_explicit:
                level = 2
                if old_id is not None:
                    old_node = self.ids[old_id]
                    if 'refuri' in node:
                        refuri = node['refuri']
                        if (old_node['names']
                            and 'refuri' in old_node
                            and old_node['refuri'] == refuri):
                            level = 1   # just inform if refuri's identical
                    if level > 1:
                        dupname(old_node, name)
                        self.nameids[name] = None
                msg = self.reporter.system_message(
                    level, 'Duplicate explicit target name: "%s".' % name,
                    backrefs=[id], base_node=node)
                if msgnode is not None:
                    msgnode += msg
                dupname(node, name)
            else:
                self.nameids[name] = id
                if old_id is not None:
                    old_node = self.ids[old_id]
                    dupname(old_node, name)
        else:
            if old_id is not None and not old_explicit:
                self.nameids[name] = None
                old_node = self.ids[old_id]
                dupname(old_node, name)
            dupname(node, name)
        if not explicit or (not old_explicit and old_id is not None):
            msg = self.reporter.info(
                'Duplicate implicit target name: "%s".' % name,
                backrefs=[id], base_node=node)
            if msgnode is not None:
                msgnode += msg

    def has_name(self, name):
        return name in self.nameids

    # "note" here is an imperative verb: "take note of".
    def note_implicit_target(self, target, msgnode=None):
        id = self.set_id(target, msgnode)
        self.set_name_id_map(target, id, msgnode, explicit=False)

    def note_explicit_target(self, target, msgnode=None):
        id = self.set_id(target, msgnode)
        self.set_name_id_map(target, id, msgnode, explicit=True)

    def note_refname(self, node):
        self.refnames.setdefault(node['refname'], []).append(node)

    def note_refid(self, node):
        self.refids.setdefault(node['refid'], []).append(node)

    def note_indirect_target(self, target):
        self.indirect_targets.append(target)
        if target['names']:
            self.note_refname(target)

    def note_anonymous_target(self, target):
        self.set_id(target)

    def note_autofootnote(self, footnote):
        self.set_id(footnote)
        self.autofootnotes.append(footnote)

    def note_autofootnote_ref(self, ref):
        self.set_id(ref)
        self.autofootnote_refs.append(ref)

    def note_symbol_footnote(self, footnote):
        self.set_id(footnote)
        self.symbol_footnotes.append(footnote)

    def note_symbol_footnote_ref(self, ref):
        self.set_id(ref)
        self.symbol_footnote_refs.append(ref)

    def note_footnote(self, footnote):
        self.set_id(footnote)
        self.footnotes.append(footnote)

    def note_footnote_ref(self, ref):
        self.set_id(ref)
        self.footnote_refs.setdefault(ref['refname'], []).append(ref)
        self.note_refname(ref)

    def note_citation(self, citation):
        self.citations.append(citation)

    def note_citation_ref(self, ref):
        self.set_id(ref)
        self.citation_refs.setdefault(ref['refname'], []).append(ref)
        self.note_refname(ref)

    def note_substitution_def(self, subdef, def_name, msgnode=None):
        name = whitespace_normalize_name(def_name)
        if name in self.substitution_defs:
            msg = self.reporter.error(
                      'Duplicate substitution definition name: "%s".' % name,
                      base_node=subdef)
            if msgnode is not None:
                msgnode += msg
            oldnode = self.substitution_defs[name]
            dupname(oldnode, name)
        # keep only the last definition:
        self.substitution_defs[name] = subdef
        # case-insensitive mapping:
        self.substitution_names[fully_normalize_name(name)] = name

    def note_substitution_ref(self, subref, refname):
        subref['refname'] = whitespace_normalize_name(refname)

    def note_pending(self, pending, priority=None):
        self.transformer.add_pending(pending, priority)

    def note_parse_message(self, message):
        self.parse_messages.append(message)

    def note_transform_message(self, message):
        self.transform_messages.append(message)

    def note_source(self, source, offset):
        self.current_source = source
        if offset is None:
            self.current_line = offset
        else:
            self.current_line = offset + 1

    def copy(self):
        obj = self.__class__(self.settings, self.reporter,
                             **self.attributes)
        obj.source = self.source
        obj.line = self.line
        return obj

    def get_decoration(self):
        if not self.decoration:
            self.decoration = decoration()
            index = self.first_child_not_matching_class((Titular, meta))
            if index is None:
                self.append(self.decoration)
            else:
                self.insert(index, self.decoration)
        return self.decoration


# Bibliographic Elements
# ======================

class author(Bibliographic, TextElement): pass
class organization(Bibliographic, TextElement): pass
class address(Bibliographic, FixedTextElement): pass
class contact(Bibliographic, TextElement): pass
class version(Bibliographic, TextElement): pass
class revision(Bibliographic, TextElement): pass
class status(Bibliographic, TextElement): pass
class date(Bibliographic, TextElement): pass
class copyright(Bibliographic, TextElement): pass


class authors(Bibliographic, Element):
    """Container for author information for documents with multiple authors.
    """
    content_model = (  # (author, organization?, address?, contact?)+
                     (author, '+'),
                     (organization, '?'),
                     (address, '?'),
                     (contact, '?'))

    def validate_content(self):
        """Repeatedly test for children matching the content model.

        Provisional.
        """
        relics = super().validate_content()
        while relics:
            relics = super().validate_content(elements=relics)
        return relics


# Body Elements
# =============
#
# General
# -------
#
# Miscellaneous Body Elements and related Body Subelements (Part)

class paragraph(General, TextElement): pass
class rubric(Titular, General, TextElement): pass


class compound(General, Element):
    content_model = ((Body, '+'),)  # (%body.elements;)+


class container(General, Element):
    content_model = ((Body, '+'),)  # (%body.elements;)+


class attribution(Part, TextElement):
    """Visible reference to the source of a `block_quote`."""


class block_quote(General, Element):
    """An extended quotation, set off from the main text."""
    content_model = (  # ((%body.elements;)+, attribution?)
                     (Body, '+'),
                     (attribution, '?'))


# Lists
# -----
#
# Lists (Sequential) and related Body Subelements (Part)

class list_item(Part, Element):
    content_model = ((Body, '*'),)  # (%body.elements;)*


class bullet_list(Sequential, Element):
    valid_attributes = Element.valid_attributes + ('bullet',)
    content_model = ((list_item, '+'),)  # (list_item+)


class enumerated_list(Sequential, Element):
    valid_attributes = Element.valid_attributes + (
        'enumtype', 'prefix', 'suffix', 'start')
    content_model = ((list_item, '+'),)  # (list_item+)


class term(Part, TextElement): pass
class classifier(Part, TextElement): pass


class definition(Part, Element):
    """Definition of a `term` in a `definition_list`."""
    content_model = ((Body, '+'),)  # (%body.elements;)+


class definition_list_item(Part, Element):
    content_model = (  # (term, classifier*, definition)
                     (term, '.'),
                     (classifier, '*'),
                     (definition, '.'))


class definition_list(Sequential, Element):
    """List of terms and their definitions.

    Can be used for glossaries or dictionaries, to describe or
    classify things, for dialogues, or to itemize subtopics.
    """
    content_model = ((definition_list_item, '+'),)  # (definition_list_item+)


class field_name(Part, TextElement): pass


class field_body(Part, Element):
    content_model = ((Body, '*'),)  # (%body.elements;)*


class field(Part, Bibliographic, Element):
    content_model = (  # (field_name, field_body)
                     (field_name, '.'),
                     (field_body, '.'))


class field_list(Sequential, Element):
    """List of label & data pairs.

    Typically rendered as a two-column list.
    Also used for extension syntax or special processing.
    """
    content_model = ((field, '+'),)  # (field+)


class option_string(Part, PureTextElement):
    """A literal command-line option. Typically monospaced."""


class option_argument(Part, PureTextElement):
    """Placeholder text for option arguments."""
    valid_attributes = Element.valid_attributes + ('delimiter',)

    def astext(self):
        return self.get('delimiter', ' ') + TextElement.astext(self)


class option(Part, Element):
    """Option element in an `option_list_item`.

    Groups an option string with zero or more option argument placeholders.
    """
    child_text_separator = ''
    content_model = (  # (option_string, option_argument*)
                     (option_string, '.'),
                     (option_argument, '*'))


class option_group(Part, Element):
    """Groups together one or more `option` elements, all synonyms."""
    child_text_separator = ', '
    content_model = ((option, '+'),)  # (option+)


class description(Part, Element):
    """Describtion of a command-line option."""
    content_model = ((Body, '+'),)  # (%body.elements;)+


class option_list_item(Part, Element):
    """Container for a pair of `option_group` and `description` elements.
    """
    child_text_separator = '  '
    content_model = (  # (option_group, description)
                     (option_group, '.'),
                     (description, '.'))


class option_list(Sequential, Element):
    """Two-column list of command-line options and descriptions."""
    content_model = ((option_list_item, '+'),)  # (option_list_item+)


# Pre-formatted text blocks
# -------------------------

class literal_block(General, FixedTextElement): pass
class doctest_block(General, FixedTextElement): pass


class math_block(General, FixedTextElement, PureTextElement):
    """Mathematical notation (display formula)."""


class line(Part, TextElement):
    """Single line of text in a `line_block`."""
    indent = None


class line_block(General, Element):
    """Sequence of lines and nested line blocks.
    """
    # recursive content model: (line | line_block)+


line_block.content_model = (((line, line_block), '+'),)


# Admonitions
# -----------
# distinctive and self-contained notices

class attention(Admonition, Element): pass
class caution(Admonition, Element): pass
class danger(Admonition, Element): pass
class error(Admonition, Element): pass
class important(Admonition, Element): pass
class note(Admonition, Element): pass
class tip(Admonition, Element): pass
class hint(Admonition, Element): pass
class warning(Admonition, Element): pass


class admonition(Admonition, Element):
    content_model = (  # (title, (%body.elements;)+)
                     (title, '.'),
                     (Body, '+'))


# Footnote and citation
# ---------------------

class label(Part, PureTextElement):
    """Visible identifier for footnotes and citations."""


class footnote(General, BackLinkable, Element, Labeled, Targetable):
    """Labelled note providing additional context (footnote or endnote)."""
    valid_attributes = Element.valid_attributes + ('auto', 'backrefs')
    content_model = (  # (label?, (%body.elements;)+)
                     (label, '?'),
                     (Body, '+'))
    # TODO: Why is the label optional and content required?
    # The rST specification says: "Each footnote consists of an
    # explicit markup start (".. "), a left square bracket,
    # the footnote label, a right square bracket, and whitespace,
    # followed by indented body elements."
    #
    # The `Labeled` parent class' docstring says:
    # "Contains a `label` as its first element."
    #
    # docutils.dtd requires both label and content but the rST parser
    # allows empty footnotes (see test_writers/test_latex2e.py).
    # Should the rST parser complain (info, warning or error)?


class citation(General, BackLinkable, Element, Labeled, Targetable):
    content_model = (  # (label, (%body.elements;)+)
                     (label, '.'),
                     (Body, '+'))
    # TODO: docutils.dtd requires both label and content but the rST parser
    # allows empty citation (see test_rst/test_citations.py).
    # Is this sensible?
    # The rST specification says: "Citations are identical to footnotes
    # except that they use only non-numeric labels such as [note] …"


# Graphical elements
# ------------------

class image(General, Inline, Element):
    """Reference to an image resource.

    May be body element or inline element.
    """
    valid_attributes = Element.valid_attributes + (
        'uri', 'alt', 'align', 'height', 'width', 'scale', 'loading')

    def astext(self):
        return self.get('alt', '')


class caption(Part, TextElement): pass


class legend(Part, Element):
    """A wrapper for text accompanying a `figure` that is not the caption."""
    content_model = ((Body, '+'),)  # (%body.elements;)+


class figure(General, Element):
    """A formal figure, generally an illustration, with a title."""
    valid_attributes = Element.valid_attributes + ('align', 'width')
    content_model = (  # (image, ((caption, legend?) | legend))
                     (image, '.'),
                     (caption, '?'),
                     (legend, '?'))
    # TODO: According to the DTD, a caption or legend is required
    # but rST allows "bare" figures which are formatted differently from
    # images (floating in LaTeX, nested in a <figure> in HTML).


# Tables
# ------

class entry(Part, Element):
    """An entry in a `row` (a table cell)."""
    valid_attributes = Element.valid_attributes + (
        'align', 'char', 'charoff', 'colname', 'colsep', 'morecols',
        'morerows', 'namest', 'nameend', 'rowsep', 'valign')
    content_model = ((Body, '*'),)  # %tbl.entry.mdl -> (%body.elements;)*


class row(Part, Element):
    """Row of table cells."""
    valid_attributes = Element.valid_attributes + ('rowsep', 'valign')
    content_model = ((entry, '+'),)  # (%tbl.row.mdl;) -> entry+


class colspec(Part, Element):
    """Specifications for a column in a `tgroup`."""
    valid_attributes = Element.valid_attributes + (
        'align', 'char', 'charoff', 'colname', 'colnum',
        'colsep', 'colwidth', 'rowsep', 'stub')


class thead(Part, Element):
    """Row(s) that form the head of a `tgroup`."""
    valid_attributes = Element.valid_attributes + ('valign',)
    content_model = ((row, '+'),)  # (row+)


class tbody(Part, Element):
    """Body of a `tgroup`."""
    valid_attributes = Element.valid_attributes + ('valign',)
    content_model = ((row, '+'),)  # (row+)


class tgroup(Part, Element):
    """A portion of a table. Most tables have just one `tgroup`."""
    valid_attributes = Element.valid_attributes + (
        'align', 'cols', 'colsep', 'rowsep')
    content_model = (  # (colspec*, thead?, tbody)
                     (colspec, '*'),
                     (thead, '?'),
                     (tbody, '.'))


class table(General, Element):
    """A data arrangement with rows and columns."""
    valid_attributes = Element.valid_attributes + (
        'align', 'colsep', 'frame', 'pgwide', 'rowsep', 'width')
    content_model = (  # (title?, tgroup+)
                     (title, '?'),
                     (tgroup, '+'))


# Special purpose elements
# ------------------------
# Body elements for internal use or special requests.

class comment(Invisible, FixedTextElement, PureTextElement):
    """Author notes, hidden from the output."""


class substitution_definition(Invisible, TextElement):
    valid_attributes = Element.valid_attributes + ('ltrim', 'rtrim')


class target(Invisible, Inline, TextElement, Targetable):
    valid_attributes = Element.valid_attributes + (
        'anonymous', 'refid', 'refname', 'refuri')


class system_message(Special, BackLinkable, PreBibliographic, Element):
    """
    System message element.

    Do not instantiate this class directly; use
    ``document.reporter.info/warning/error/severe()`` instead.
    """
    valid_attributes = BackLinkable.valid_attributes + (
                           'level', 'line', 'type')
    content_model = ((Body, '+'),)  # (%body.elements;)+

    def __init__(self, message=None, *children, **attributes):
        rawsource = attributes.pop('rawsource', '')
        if message:
            p = paragraph('', message)
            children = (p,) + children
        try:
            Element.__init__(self, rawsource, *children, **attributes)
        except:  # noqa  catchall
            print('system_message: children=%r' % (children,))
            raise

    def astext(self):
        line = self.get('line', '')
        return '%s:%s: (%s/%s) %s' % (self['source'], line, self['type'],
                                      self['level'], Element.astext(self))


class pending(Invisible, Element):
    """
    Placeholder for pending operations.

    The "pending" element is used to encapsulate a pending operation: the
    operation (transform), the point at which to apply it, and any data it
    requires.  Only the pending operation's location within the document is
    stored in the public document tree (by the "pending" object itself); the
    operation and its data are stored in the "pending" object's internal
    instance attributes.

    For example, say you want a table of contents in your reStructuredText
    document.  The easiest way to specify where to put it is from within the
    document, with a directive::

        .. contents::

    But the "contents" directive can't do its work until the entire document
    has been parsed and possibly transformed to some extent.  So the directive
    code leaves a placeholder behind that will trigger the second phase of its
    processing, something like this::

        <pending ...public attributes...> + internal attributes

    Use `document.note_pending()` so that the
    `docutils.transforms.Transformer` stage of processing can run all pending
    transforms.
    """

    def __init__(self, transform, details=None,
                 rawsource='', *children, **attributes):
        Element.__init__(self, rawsource, *children, **attributes)

        self.transform = transform
        """The `docutils.transforms.Transform` class implementing the pending
        operation."""

        self.details = details or {}
        """Detail data (dictionary) required by the pending operation."""

    def pformat(self, indent='    ', level=0):
        internals = ['.. internal attributes:',
                     '     .transform: %s.%s' % (self.transform.__module__,
                                                 self.transform.__name__),
                     '     .details:']
        details = sorted(self.details.items())
        for key, value in details:
            if isinstance(value, Node):
                internals.append('%7s%s:' % ('', key))
                internals.extend(['%9s%s' % ('', line)
                                  for line in value.pformat().splitlines()])
            elif (value
                  and isinstance(value, list)
                  and isinstance(value[0], Node)):
                internals.append('%7s%s:' % ('', key))
                for v in value:
                    internals.extend(['%9s%s' % ('', line)
                                      for line in v.pformat().splitlines()])
            else:
                internals.append('%7s%s: %r' % ('', key, value))
        return (Element.pformat(self, indent, level)
                + ''.join(('    %s%s\n' % (indent * level, line))
                          for line in internals))

    def copy(self):
        obj = self.__class__(self.transform, self.details, self.rawsource,
                             **self.attributes)
        obj._document = self._document
        obj.source = self.source
        obj.line = self.line
        return obj


class raw(Special, Inline, PreBibliographic,
          FixedTextElement, PureTextElement):
    """Raw data that is to be passed untouched to the Writer.

    Can be used as Body element or Inline element.
    """
    valid_attributes = Element.valid_attributes + ('format', 'xml:space')


# Inline Elements
# ===============

class abbreviation(Inline, TextElement): pass
class acronym(Inline, TextElement): pass
class emphasis(Inline, TextElement): pass
class generated(Inline, TextElement): pass
class inline(Inline, TextElement): pass
class literal(Inline, TextElement): pass
class strong(Inline, TextElement): pass
class subscript(Inline, TextElement): pass
class superscript(Inline, TextElement): pass
class title_reference(Inline, TextElement): pass


class reference(General, Inline, Referential, TextElement):
    valid_attributes = Element.valid_attributes + (
        'anonymous', 'name', 'refid', 'refname', 'refuri')


class footnote_reference(Inline, Referential, PureTextElement):
    valid_attributes = Element.valid_attributes + ('auto', 'refid', 'refname')


class citation_reference(Inline, Referential, PureTextElement):
    valid_attributes = Element.valid_attributes + ('refid', 'refname')


class substitution_reference(Inline, TextElement):
    valid_attributes = Element.valid_attributes + ('refname',)


class math(Inline, PureTextElement):
    """Mathematical notation in running text."""


class problematic(Inline, TextElement):
    valid_attributes = Element.valid_attributes + (
                           'refid', 'refname', 'refuri')


# ========================================
#  Auxiliary Classes, Functions, and Data
# ========================================

node_class_names = """
    Text
    abbreviation acronym address admonition attention attribution author
        authors
    block_quote bullet_list
    caption caution citation citation_reference classifier colspec comment
        compound contact container copyright
    danger date decoration definition definition_list definition_list_item
        description docinfo doctest_block document
    emphasis entry enumerated_list error
    field field_body field_list field_name figure footer
        footnote footnote_reference
    generated
    header hint
    image important inline
    label legend line line_block list_item literal literal_block
    math math_block meta
    note
    option option_argument option_group option_list option_list_item
        option_string organization
    paragraph pending problematic
    raw reference revision row rubric
    section sidebar status strong subscript substitution_definition
        substitution_reference subtitle superscript system_message
    table target tbody term tgroup thead tip title title_reference topic
        transition
    version
    warning""".split()
"""A list of names of all concrete Node subclasses."""


class NodeVisitor:
    """
    "Visitor" pattern [GoF95]_ abstract superclass implementation for
    document tree traversals.

    Each node class has corresponding methods, doing nothing by
    default; override individual methods for specific and useful
    behaviour.  The `dispatch_visit()` method is called by
    `Node.walk()` upon entering a node.  `Node.walkabout()` also calls
    the `dispatch_departure()` method before exiting a node.

    The dispatch methods call "``visit_`` + node class name" or
    "``depart_`` + node class name", resp.

    This is a base class for visitors whose ``visit_...`` & ``depart_...``
    methods must be implemented for *all* compulsory node types encountered
    (such as for `docutils.writers.Writer` subclasses).
    Unimplemented methods will raise exceptions (except for optional nodes).

    For sparse traversals, where only certain node types are of interest, use
    subclass `SparseNodeVisitor` instead.  When (mostly or entirely) uniform
    processing is desired, subclass `GenericNodeVisitor`.

    .. [GoF95] Gamma, Helm, Johnson, Vlissides. *Design Patterns: Elements of
       Reusable Object-Oriented Software*. Addison-Wesley, Reading, MA, USA,
       1995.
    """

    optional = ('meta',)
    """
    Tuple containing node class names (as strings).

    No exception will be raised if writers do not implement visit
    or departure functions for these node classes.

    Used to ensure transitional compatibility with existing 3rd-party writers.
    """

    def __init__(self, document):
        self.document = document

    def dispatch_visit(self, node):
        """
        Call self."``visit_`` + node class name" with `node` as
        parameter.  If the ``visit_...`` method does not exist, call
        self.unknown_visit.
        """
        node_name = node.__class__.__name__
        method = getattr(self, 'visit_' + node_name, self.unknown_visit)
        self.document.reporter.debug(
            'docutils.nodes.NodeVisitor.dispatch_visit calling %s for %s'
            % (method.__name__, node_name))
        return method(node)

    def dispatch_departure(self, node):
        """
        Call self."``depart_`` + node class name" with `node` as
        parameter.  If the ``depart_...`` method does not exist, call
        self.unknown_departure.
        """
        node_name = node.__class__.__name__
        method = getattr(self, 'depart_' + node_name, self.unknown_departure)
        self.document.reporter.debug(
            'docutils.nodes.NodeVisitor.dispatch_departure calling %s for %s'
            % (method.__name__, node_name))
        return method(node)

    def unknown_visit(self, node):
        """
        Called when entering unknown `Node` types.

        Raise an exception unless overridden.
        """
        if (self.document.settings.strict_visitor
            or node.__class__.__name__ not in self.optional):
            raise NotImplementedError(
                '%s visiting unknown node type: %s'
                % (self.__class__, node.__class__.__name__))

    def unknown_departure(self, node):
        """
        Called before exiting unknown `Node` types.

        Raise exception unless overridden.
        """
        if (self.document.settings.strict_visitor
            or node.__class__.__name__ not in self.optional):
            raise NotImplementedError(
                '%s departing unknown node type: %s'
                % (self.__class__, node.__class__.__name__))


class SparseNodeVisitor(NodeVisitor):
    """
    Base class for sparse traversals, where only certain node types are of
    interest.  When ``visit_...`` & ``depart_...`` methods should be
    implemented for *all* node types (such as for `docutils.writers.Writer`
    subclasses), subclass `NodeVisitor` instead.
    """


class GenericNodeVisitor(NodeVisitor):
    """
    Generic "Visitor" abstract superclass, for simple traversals.

    Unless overridden, each ``visit_...`` method calls `default_visit()`, and
    each ``depart_...`` method (when using `Node.walkabout()`) calls
    `default_departure()`. `default_visit()` (and `default_departure()`) must
    be overridden in subclasses.

    Define fully generic visitors by overriding `default_visit()` (and
    `default_departure()`) only. Define semi-generic visitors by overriding
    individual ``visit_...()`` (and ``depart_...()``) methods also.

    `NodeVisitor.unknown_visit()` (`NodeVisitor.unknown_departure()`) should
    be overridden for default behavior.
    """

    def default_visit(self, node):
        """Override for generic, uniform traversals."""
        raise NotImplementedError

    def default_departure(self, node):
        """Override for generic, uniform traversals."""
        raise NotImplementedError


def _call_default_visit(self, node):
    self.default_visit(node)


def _call_default_departure(self, node):
    self.default_departure(node)


def _nop(self, node):
    pass


def _add_node_class_names(names):
    """Save typing with dynamic assignments:"""
    for _name in names:
        setattr(GenericNodeVisitor, "visit_" + _name, _call_default_visit)
        setattr(GenericNodeVisitor, "depart_" + _name, _call_default_departure)
        setattr(SparseNodeVisitor, 'visit_' + _name, _nop)
        setattr(SparseNodeVisitor, 'depart_' + _name, _nop)


_add_node_class_names(node_class_names)


class TreeCopyVisitor(GenericNodeVisitor):
    """
    Make a complete copy of a tree or branch, including element attributes.
    """

    def __init__(self, document):
        GenericNodeVisitor.__init__(self, document)
        self.parent_stack = []
        self.parent = []

    def get_tree_copy(self):
        return self.parent[0]

    def default_visit(self, node):
        """Copy the current node, and make it the new acting parent."""
        newnode = node.copy()
        self.parent.append(newnode)
        self.parent_stack.append(self.parent)
        self.parent = newnode

    def default_departure(self, node):
        """Restore the previous acting parent."""
        self.parent = self.parent_stack.pop()


# Custom Exceptions
# =================

class ValidationError(ValueError):
    """Invalid Docutils Document Tree Element."""
    def __init__(self, msg, problematic_element=None):
        super().__init__(msg)
        self.problematic_element = problematic_element


class TreePruningException(Exception):
    """
    Base class for `NodeVisitor`-related tree pruning exceptions.

    Raise subclasses from within ``visit_...`` or ``depart_...`` methods
    called from `Node.walk()` and `Node.walkabout()` tree traversals to prune
    the tree traversed.
    """


class SkipChildren(TreePruningException):
    """
    Do not visit any children of the current node.  The current node's
    siblings and ``depart_...`` method are not affected.
    """


class SkipSiblings(TreePruningException):
    """
    Do not visit any more siblings (to the right) of the current node.  The
    current node's children and its ``depart_...`` method are not affected.
    """


class SkipNode(TreePruningException):
    """
    Do not visit the current node's children, and do not call the current
    node's ``depart_...`` method.
    """


class SkipDeparture(TreePruningException):
    """
    Do not call the current node's ``depart_...`` method.  The current node's
    children and siblings are not affected.
    """


class NodeFound(TreePruningException):
    """
    Raise to indicate that the target of a search has been found.  This
    exception must be caught by the client; it is not caught by the traversal
    code.
    """


class StopTraversal(TreePruningException):
    """
    Stop the traversal altogether.  The current node's ``depart_...`` method
    is not affected.  The parent nodes ``depart_...`` methods are also called
    as usual.  No other nodes are visited.  This is an alternative to
    NodeFound that does not cause exception handling to trickle up to the
    caller.
    """


# definition moved here from `utils` to avoid circular import dependency
def unescape(text, restore_backslashes=False, respect_whitespace=False):
    """
    Return a string with nulls removed or restored to backslashes.
    Backslash-escaped spaces are also removed.
    """
    # `respect_whitespace` is ignored (since introduction 2016-12-16)
    if restore_backslashes:
        return text.replace('\x00', '\\')
    else:
        for sep in ['\x00 ', '\x00\n', '\x00']:
            text = ''.join(text.split(sep))
        return text


def make_id(string):
    """
    Convert `string` into an identifier and return it.

    Docutils identifiers will conform to the regular expression
    ``[a-z](-?[a-z0-9]+)*``.  For CSS compatibility, identifiers (the "class"
    and "id" attributes) should have no underscores, colons, or periods.
    Hyphens may be used.

    - The `HTML 4.01 spec`_ defines identifiers based on SGML tokens:

          ID and NAME tokens must begin with a letter ([A-Za-z]) and may be
          followed by any number of letters, digits ([0-9]), hyphens ("-"),
          underscores ("_"), colons (":"), and periods (".").

    - However the `CSS1 spec`_ defines identifiers based on the "name" token,
      a tighter interpretation ("flex" tokenizer notation; "latin1" and
      "escape" 8-bit characters have been replaced with entities)::

          unicode     \\[0-9a-f]{1,4}
          latin1      [&iexcl;-&yuml;]
          escape      {unicode}|\\[ -~&iexcl;-&yuml;]
          nmchar      [-a-z0-9]|{latin1}|{escape}
          name        {nmchar}+

    The CSS1 "nmchar" rule does not include underscores ("_"), colons (":"),
    or periods ("."), therefore "class" and "id" attributes should not contain
    these characters. They should be replaced with hyphens ("-"). Combined
    with HTML's requirements (the first character must be a letter; no
    "unicode", "latin1", or "escape" characters), this results in the
    ``[a-z](-?[a-z0-9]+)*`` pattern.

    .. _HTML 4.01 spec: https://www.w3.org/TR/html401
    .. _CSS1 spec: https://www.w3.org/TR/REC-CSS1
    """
    id = string.lower()
    id = id.translate(_non_id_translate_digraphs)
    id = id.translate(_non_id_translate)
    # get rid of non-ascii characters.
    # 'ascii' lowercase to prevent problems with turkish locale.
    id = unicodedata.normalize(
            'NFKD', id).encode('ascii', 'ignore').decode('ascii')
    # shrink runs of whitespace and replace by hyphen
    id = _non_id_chars.sub('-', ' '.join(id.split()))
    id = _non_id_at_ends.sub('', id)
    return str(id)


_non_id_chars = re.compile('[^a-z0-9]+')
_non_id_at_ends = re.compile('^[-0-9]+|-+$')
_non_id_translate = {
    0x00f8: 'o',       # o with stroke
    0x0111: 'd',       # d with stroke
    0x0127: 'h',       # h with stroke
    0x0131: 'i',       # dotless i
    0x0142: 'l',       # l with stroke
    0x0167: 't',       # t with stroke
    0x0180: 'b',       # b with stroke
    0x0183: 'b',       # b with topbar
    0x0188: 'c',       # c with hook
    0x018c: 'd',       # d with topbar
    0x0192: 'f',       # f with hook
    0x0199: 'k',       # k with hook
    0x019a: 'l',       # l with bar
    0x019e: 'n',       # n with long right leg
    0x01a5: 'p',       # p with hook
    0x01ab: 't',       # t with palatal hook
    0x01ad: 't',       # t with hook
    0x01b4: 'y',       # y with hook
    0x01b6: 'z',       # z with stroke
    0x01e5: 'g',       # g with stroke
    0x0225: 'z',       # z with hook
    0x0234: 'l',       # l with curl
    0x0235: 'n',       # n with curl
    0x0236: 't',       # t with curl
    0x0237: 'j',       # dotless j
    0x023c: 'c',       # c with stroke
    0x023f: 's',       # s with swash tail
    0x0240: 'z',       # z with swash tail
    0x0247: 'e',       # e with stroke
    0x0249: 'j',       # j with stroke
    0x024b: 'q',       # q with hook tail
    0x024d: 'r',       # r with stroke
    0x024f: 'y',       # y with stroke
}
_non_id_translate_digraphs = {
    0x00df: 'sz',      # ligature sz
    0x00e6: 'ae',      # ae
    0x0153: 'oe',      # ligature oe
    0x0238: 'db',      # db digraph
    0x0239: 'qp',      # qp digraph
}


def dupname(node, name):
    node['dupnames'].append(name)
    node['names'].remove(name)
    # Assume that `node` is referenced, even though it isn't;
    # we don't want to throw unnecessary system_messages.
    node.referenced = True


def fully_normalize_name(name):
    """Return a case- and whitespace-normalized name."""
    return ' '.join(name.lower().split())


def whitespace_normalize_name(name):
    """Return a whitespace-normalized name."""
    return ' '.join(name.split())


def serial_escape(value):
    """Escape string values that are elements of a list, for serialization."""
    return value.replace('\\', r'\\').replace(' ', r'\ ')


def split_name_list(s):
    r"""Split a string at non-escaped whitespace.

    Backslashes escape internal whitespace (cf. `serial_escape()`).
    Return list of "names" (after removing escaping backslashes).

    >>> split_name_list(r'a\ n\ame two\\ n\\ames'),
    ['a name', 'two\\', r'n\ames']

    Provisional.
    """
    s = s.replace('\\', '\x00')         # escape with NULL char
    s = s.replace('\x00\x00', '\\')     # unescape backslashes
    s = s.replace('\x00 ', '\x00\x00')  # escaped spaces -> NULL NULL
    names = s.split(' ')
    # restore internal spaces, drop other escaping characters
    return [name.replace('\x00\x00', ' ').replace('\x00', '')
            for name in names]


def pseudo_quoteattr(value):
    """Quote attributes for pseudo-xml"""
    return '"%s"' % value


# Methods to validate `Element attribute`__ values.

# Ensure the expected Python `data type`__, normalize, and check for
# restrictions.
#
# The methods can be used to convert `str` values (eg. from an XML
# representation) or to validate an existing document tree or node.
#
# Cf. `Element.validate_attributes()`, `docutils.parsers.docutils_xml`,
# and the `attribute_validating_functions` mapping below.
#
# __ https://docutils.sourceforge.io/docs/ref/doctree.html#attribute-reference
# __ https://docutils.sourceforge.io/docs/ref/doctree.html#attribute-types

def validate_enumerated_type(*keywords):
    """
    Return a function that validates a `str` against given `keywords`.

    Provisional.
    """
    def validate_keywords(value):
        if value not in keywords:
            allowed = '", \"'.join(keywords)
            raise ValueError(f'"{value}" is not one of "{allowed}".')
        return value
    return validate_keywords


def validate_identifier(value):
    """
    Validate identifier key or class name.

    Used in `idref.type`__ and for the tokens in `validate_identifier_list()`.

    __ https://docutils.sourceforge.io/docs/ref/doctree.html#idref-type

    Provisional.
    """
    if value != make_id(value):
        raise ValueError(f'"{value}" is no valid id or class name.')
    return value


def validate_identifier_list(value):
    """
    A (space-separated) list of ids or class names.

    `value` may be a `list` or a `str` with space separated
    ids or class names (cf. `validate_identifier()`).

    Used in `classnames.type`__, `ids.type`__, and `idrefs.type`__.

    __ https://docutils.sourceforge.io/docs/ref/doctree.html#classnames-type
    __ https://docutils.sourceforge.io/docs/ref/doctree.html#ids-type
    __ https://docutils.sourceforge.io/docs/ref/doctree.html#idrefs-type

    Provisional.
    """
    if isinstance(value, str):
        value = value.split()
    for token in value:
        validate_identifier(token)
    return value


def validate_measure(value):
    """
    Validate a length measure__ (number + recognized unit).

    __ https://docutils.sourceforge.io/docs/ref/doctree.html#measure

    Provisional.
    """
    units = 'em|ex|px|in|cm|mm|pt|pc|%'
    if not re.fullmatch(f'[-0-9.]+ *({units}?)', value):
        raise ValueError(f'"{value}" is no valid measure. '
                         f'Valid units: {units.replace("|", " ")}.')
    return value.replace(' ', '').strip()


def validate_NMTOKEN(value):
    """
    Validate a "name token": a `str` of letters, digits, and [-._].

    Provisional.
    """
    if not re.fullmatch('[-._A-Za-z0-9]+', value):
        raise ValueError(f'"{value}" is no NMTOKEN.')
    return value


def validate_NMTOKENS(value):
    """
    Validate a list of "name tokens".

    Provisional.
    """
    if isinstance(value, str):
        value = value.split()
    for token in value:
        validate_NMTOKEN(token)
    return value


def validate_refname_list(value):
    """
    Validate a list of `reference names`__.

    Reference names may contain all characters;
    whitespace is normalized (cf, `whitespace_normalize_name()`).

    `value` may be either a `list` of names or a `str` with
    space separated names (with internal spaces backslash escaped
    and literal backslashes doubled cf. `serial_escape()`).

    Return a list of whitespace-normalized, unescaped reference names.

    Provisional.

    __ https://docutils.sourceforge.io/docs/ref/doctree.html#reference-name
    """
    if isinstance(value, str):
        value = split_name_list(value)
    return [whitespace_normalize_name(name) for name in value]


def validate_yesorno(value):
    if value == "0":
        return False
    return bool(value)


ATTRIBUTE_VALIDATORS = {
    'alt': str,  # CDATA
    'align': str,
    'anonymous': validate_yesorno,
    'auto': str,  # CDATA (only '1' or '*' are used in rST)
    'backrefs': validate_identifier_list,
    'bullet': str,  # CDATA (only '-', '+', or '*' are used in rST)
    'classes': validate_identifier_list,
    'char': str,  # from Exchange Table Model (CALS), currently ignored
    'charoff': validate_NMTOKEN,  # from CALS, currently ignored
    'colname': validate_NMTOKEN,  # from CALS, currently ignored
    'colnum': int,  # from CALS, currently ignored
    'cols': int,  # from CALS: "NMTOKEN, […] must be an integer > 0".
    'colsep': validate_yesorno,
    'colwidth': int,  # sic! CALS: CDATA (measure or number+'*')
    'content': str,  # <meta>
    'delimiter': str,
    'dir': validate_enumerated_type('ltr', 'rtl', 'auto'),  # <meta>
    'dupnames': validate_refname_list,
    'enumtype': validate_enumerated_type('arabic', 'loweralpha', 'lowerroman',
                                         'upperalpha', 'upperroman'),
    'format': str,  # CDATA (space separated format names)
    'frame': validate_enumerated_type('top', 'bottom', 'topbot', 'all',
                                      'sides', 'none'),  # from CALS, ignored
    'height': validate_measure,
    'http-equiv': str,  # <meta>
    'ids': validate_identifier_list,
    'lang': str,  # <meta>
    'level': int,
    'line': int,
    'ltrim': validate_yesorno,
    'loading': validate_enumerated_type('embed', 'link', 'lazy'),
    'media': str,  # <meta>
    'morecols': int,
    'morerows': int,
    'name': whitespace_normalize_name,  # in <reference> (deprecated)
    # 'name': node_attributes.validate_NMTOKEN,  # in <meta>
    'names': validate_refname_list,
    'namest': validate_NMTOKEN,  # start of span, from CALS, currently ignored
    'nameend': validate_NMTOKEN,  # end of span, from CALS, currently ignored
    'pgwide': validate_yesorno,  # from CALS, currently ignored
    'prefix': str,
    'refid': validate_identifier,
    'refname': whitespace_normalize_name,
    'refuri': str,
    'rowsep': validate_yesorno,
    'rtrim': validate_yesorno,
    'scale': int,
    'scheme': str,
    'source': str,
    'start': int,
    'stub': validate_yesorno,
    'suffix': str,
    'title': str,
    'type': validate_NMTOKEN,
    'uri': str,
    'valign': validate_enumerated_type('top', 'middle', 'bottom'),  # from CALS
    'width': validate_measure,
    'xml:space': validate_enumerated_type('default', 'preserve'),
    }
"""
Mapping of `attribute names`__ to validating functions.

Provisional.

__ https://docutils.sourceforge.io/docs/ref/doctree.html#attribute-reference
"""
