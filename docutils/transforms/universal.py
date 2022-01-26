# $Id$
# Authors: David Goodger <goodger@python.org>; Ueli Schlaepfer; Günter Milde
# Maintainer: docutils-develop@lists.sourceforge.net
# Copyright: This module has been placed in the public domain.

"""
Transforms needed by most or all documents:

- `Decorations`: Generate a document's header & footer.
- `ExposeInternals`: Expose internal attributes.
- `Messages`: Placement of system messages generated after parsing.
- `FilterMessages`: Remove system messages below verbosity threshold.
- `TestMessages`: Like `Messages`, used on test runs.
- `StripComments`: Remove comment elements from the document tree.
- `StripClassesAndElements`: Remove elements with classes
  in `self.document.settings.strip_elements_with_classes`
  and class values in `self.document.settings.strip_classes`.
- `SmartQuotes`: Replace ASCII quotation marks with typographic form.
"""

__docformat__ = 'reStructuredText'

import re
import sys
import time
from docutils import nodes, utils
from docutils.transforms import TransformError, Transform
from docutils.utils import smartquotes


class Decorations(Transform):

    """
    Populate a document's decoration element (header, footer).
    """

    default_priority = 820

    def apply(self):
        header_nodes = self.generate_header()
        if header_nodes:
            decoration = self.document.get_decoration()
            header = decoration.get_header()
            header.extend(header_nodes)
        footer_nodes = self.generate_footer()
        if footer_nodes:
            decoration = self.document.get_decoration()
            footer = decoration.get_footer()
            footer.extend(footer_nodes)

    def generate_header(self):
        return None

    def generate_footer(self):
        # @@@ Text is hard-coded for now.
        # Should be made dynamic (language-dependent).
        # @@@ Use timestamp from the `SOURCE_DATE_EPOCH`_ environment variable
        # for the datestamp?
        # See https://sourceforge.net/p/docutils/patches/132/
        # and https://reproducible-builds.org/specs/source-date-epoch/
        settings = self.document.settings
        if settings.generator or settings.datestamp or settings.source_link \
               or settings.source_url:
            text = []
            if settings.source_link and settings._source \
                   or settings.source_url:
                if settings.source_url:
                    source = settings.source_url
                else:
                    source = utils.relative_path(settings._destination,
                                                 settings._source)
                text.extend([
                    nodes.reference('', 'View document source',
                                    refuri=source),
                    nodes.Text('.\n')])
            if settings.datestamp:
                datestamp = time.strftime(settings.datestamp, time.gmtime())
                text.append(nodes.Text('Generated on: ' + datestamp + '.\n'))
            if settings.generator:
                text.extend([
                    nodes.Text('Generated by '),
                    nodes.reference('', 'Docutils', 
                        refuri='https://docutils.sourceforge.io/'),
                    nodes.Text(' from '),
                    nodes.reference('', 'reStructuredText',
                        refuri='https://docutils.sourceforge.io/rst.html'),
                    nodes.Text(' source.\n')])
            return [nodes.paragraph('', '', *text)]
        else:
            return None


class ExposeInternals(Transform):

    """
    Expose internal attributes if ``expose_internals`` setting is set.
    """

    default_priority = 840

    def not_Text(self, node):
        return not isinstance(node, nodes.Text)

    def apply(self):
        if self.document.settings.expose_internals:
            for node in self.document.findall(self.not_Text):
                for att in self.document.settings.expose_internals:
                    value = getattr(node, att, None)
                    if value is not None:
                        node['internal:' + att] = value


class Messages(Transform):

    """
    Place any system messages generated after parsing into a dedicated section
    of the document.
    """

    default_priority = 860

    def apply(self):
        unfiltered = self.document.transform_messages
        threshold = self.document.reporter.report_level
        messages = []
        for msg in unfiltered:
            if msg['level'] >= threshold and not msg.parent:
                messages.append(msg)
        if messages:
            section = nodes.section(classes=['system-messages'])
            # @@@ get this from the language module?
            section += nodes.title('', 'Docutils System Messages')
            section += messages
            self.document.transform_messages[:] = []
            self.document += section


# TODO: fix bug #435:

# Messages are filtered at a very late stage
# This breaks the link from inline error messages to the corresponding
# system message at the end of document.

class FilterMessages(Transform):

    """
    Remove system messages below verbosity threshold.
    """

    default_priority = 870

    def apply(self):
        for node in tuple(self.document.findall(nodes.system_message)):
            if node['level'] < self.document.reporter.report_level:
                node.parent.remove(node)


class TestMessages(Transform):

    """
    Append all post-parse system messages to the end of the document.

    Used for testing purposes.
    """

    default_priority = 880

    def apply(self):
        for msg in self.document.transform_messages:
            if not msg.parent:
                self.document += msg


class StripComments(Transform):

    """
    Remove comment elements from the document tree (only if the
    ``strip_comments`` setting is enabled).
    """

    default_priority = 740

    def apply(self):
        if self.document.settings.strip_comments:
            for node in tuple(self.document.findall(nodes.comment)):
                node.parent.remove(node)


class StripClassesAndElements(Transform):

    """
    Remove from the document tree all elements with classes in
    `self.document.settings.strip_elements_with_classes` and all "classes"
    attribute values in `self.document.settings.strip_classes`.
    """

    default_priority = 420

    def apply(self):
        if self.document.settings.strip_elements_with_classes:
            self.strip_elements = {*self.document.settings
                                   .strip_elements_with_classes}
            # Iterate over a tuple as removing the current node
            # corrupts the iterator returned by `iter`:
            for node in tuple(self.document.findall(self.check_classes)):
                node.parent.remove(node)

        if not self.document.settings.strip_classes:
            return
        strip_classes = self.document.settings.strip_classes
        for node in self.document.findall(nodes.Element):
            for class_value in strip_classes:
                try:
                    node['classes'].remove(class_value)
                except ValueError:
                    pass

    def check_classes(self, node):
        if not isinstance(node, nodes.Element):
            return False
        for class_value in node['classes'][:]:
            if class_value in self.strip_elements:
                return True
        return False


class SmartQuotes(Transform):

    """
    Replace ASCII quotation marks with typographic form.

    Also replace multiple dashes with em-dash/en-dash characters.
    """

    default_priority = 855

    nodes_to_skip = (nodes.FixedTextElement, nodes.Special)
    """Do not apply "smartquotes" to instances of these block-level nodes."""

    literal_nodes = (nodes.FixedTextElement, nodes.Special,
                     nodes.image, nodes.literal, nodes.math,
                     nodes.raw, nodes.problematic)
    """Do not apply smartquotes to instances of these inline nodes."""

    smartquotes_action = 'qDe'
    """Setting to select smartquote transformations.

    The default 'qDe' educates normal quote characters: (", '),
    em- and en-dashes (---, --) and ellipses (...).
    """

    def __init__(self, document, startnode):
        Transform.__init__(self, document, startnode=startnode)
        self.unsupported_languages = set()

    def get_tokens(self, txtnodes):
        # A generator that yields ``(texttype, nodetext)`` tuples for a list
        # of "Text" nodes (interface to ``smartquotes.educate_tokens()``).
        for node in txtnodes:
            if (isinstance(node.parent, self.literal_nodes)
                or isinstance(node.parent.parent, self.literal_nodes)):
                yield 'literal', str(node)
            else:
                # SmartQuotes uses backslash escapes instead of null-escapes
                # Insert backslashes before escaped "active" characters.
                txt = re.sub('(?<=\x00)([-\\\'".`])', r'\\\1', str(node))
                yield 'plain', txt

    def apply(self):
        smart_quotes = self.document.settings.setdefault('smart_quotes',
                                                           False)
        if not smart_quotes:
            return
        try:
            alternative = smart_quotes.startswith('alt')
        except AttributeError:
            alternative = False

        document_language = self.document.settings.language_code
        lc_smartquotes = self.document.settings.smartquotes_locales
        if lc_smartquotes:
            smartquotes.smartchars.quotes.update(dict(lc_smartquotes))

        # "Educate" quotes in normal text. Handle each block of text
        # (TextElement node) as a unit to keep context around inline nodes:
        for node in self.document.findall(nodes.TextElement):
            # skip preformatted text blocks and special elements:
            if isinstance(node, self.nodes_to_skip):
                continue
            # nested TextElements are not "block-level" elements:
            if isinstance(node.parent, nodes.TextElement):
                continue

            # list of text nodes in the "text block":
            txtnodes = [txtnode for txtnode in node.findall(nodes.Text)
                        if not isinstance(txtnode.parent,
                                          nodes.option_string)]

            # language: use typographical quotes for language "lang"
            lang = node.get_language_code(document_language)
            # use alternative form if `smart-quotes` setting starts with "alt":
            if alternative:
                if '-x-altquot' in lang:
                    lang = lang.replace('-x-altquot', '')
                else:
                    lang += '-x-altquot'
            # drop unsupported subtags:
            for tag in utils.normalize_language_tag(lang):
                if tag in smartquotes.smartchars.quotes:
                    lang = tag
                    break
            else: # language not supported: (keep ASCII quotes)
                if lang not in self.unsupported_languages:
                    self.document.reporter.warning('No smart quotes '
                        'defined for language "%s".'%lang, base_node=node)
                self.unsupported_languages.add(lang)
                lang = ''

            # Iterator educating quotes in plain text:
            # (see "utils/smartquotes.py" for the attribute setting)
            teacher = smartquotes.educate_tokens(self.get_tokens(txtnodes),
                                attr=self.smartquotes_action, language=lang)

            for txtnode, newtext in zip(txtnodes, teacher):
                txtnode.parent.replace(txtnode, nodes.Text(newtext))

        self.unsupported_languages = set() # reset
