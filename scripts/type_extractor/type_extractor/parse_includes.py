"""Parses header files, extract declarations of functions and struct, union, enum definitions."""

import logging
import re

from .func_info import FuncInfo
from .func_info import get_declarations
from .func_info import parse_func_declaration
from .header_text_filters import filter_oneline_typedefs
from .header_text_filters import use_filters
from .params_info import Param
from .params_info import parse_func_parameters
from .params_info import split_params
from .parse_enums import get_all_enums
from .parse_enums import parse_enum
from .parse_structs_unions import get_all_structs
from .parse_structs_unions import get_all_unions
from .parse_structs_unions import parse_struct
from .parse_structs_unions import parse_union


def get_types_info_from_text(file, content, output):
    """Extracts all function declarations and struct, union, enum definitions
    from input text.
    """
    content = use_filters(content)
    unions_content = content

    structs, content = parse_all_structs(content, file)
    unions, unions_content = parse_all_unions(unions_content, file)
    enums = parse_all_enums(content, file)
    typedefs = parse_typedefs(content)
    content = filter_oneline_typedefs(content)
    functions = parse_all_functions(content, output, file)

    functions = remove_unwanted_functions(functions)

    return functions, typedefs, structs, unions, enums


def parse_all_structs(content, file):
    """Gets structs' definitions from headers. Returns Struct objects in
    dictionary and header content without structs.
    """
    content, file_structs = get_all_structs(content)
    structs = {}
    for s in file_structs:
        struct_info = parse_struct(s, file)
        sname = struct_info.name_text
        if not sname:
            sname = struct_info.type_name_text

        if sname:
            if sname in structs:
                logging.warning(
                    f'Duplicit struct: {sname}\nFile: {struct_info.header_text}\n{struct_info.members_list}\nFirst struct was:\nFile: {structs[sname].header_text}\n{structs[sname].members_list}\n'
                )
            else:
                structs[sname] = struct_info
    return structs, content


def parse_all_unions(content, file):
    """Get unions' definitions from headers.

    Return Union objects in dictionary and header content without unions.
    """
    content, file_unions = get_all_unions(content)
    unions = {}
    for u in file_unions:
        union_info = parse_union(u, file)
        uname = union_info.name_text
        if not uname:
            uname = union_info.type_name_text

        if uname:
            if uname in unions:
                logging.warning(
                    f'Duplicit union: {uname}\nFile: {union_info.header_text}\n{union_info.members_list}\nFirst union was:\nFile: {unions[uname].header_text}\n{unions[uname].members_list}\n'
                )
            else:
                unions[uname] = union_info
    return unions, content


def parse_all_functions(content, output, file):
    """Gets functions' declarations from header file.

    Returns FuncInfo objects in dictionary.
    """
    decls = get_declarations(content)
    functions = {}
    for decl in decls:
        decl = decl.strip()

        name, ret, params, call_conv = parse_func_declaration(decl)
        if wrong_func_parameters(decl):
            continue
        if name is not None:
            varargs = False
            if params.endswith('...'):
                varargs = True
                params = params[:params.rfind(',')]
            params_list = parse_func_parameters(params)
            if varargs and output != 'json':
                params_list.append(Param('vararg', '...'))
            finfo = FuncInfo(decl, name, file, ret, params_list, varargs, call_conv)
            finfo.delete_underscores_in_param_names()
            if name in functions:
                logging.warning(
                    f'Duplicit declaration: {name}\nFile: {finfo.header}\n{finfo.decl}\nFirst declaration was:\nFile: {functions[name].header}\n{functions[name].decl}\n'
                )
            else:
                functions[name] = finfo
    return functions


def remove_unwanted_functions(functions):
    """Removes functions which we do not want in our extracted files.

    Returns a new dictionary with filtered functions.
    """
    return {
        func: func_info for func,
        func_info in functions.items() if is_wanted(func_info)
    }


def is_wanted(func_info):
    """Do we want to include the given function in our extracted files?"""
    # We do not want to include generic Windows functions whose arguments or
    # return types are "T" types (e.g. LPCTSTR). They are never present in
    # binary files. Instead, their A/W variants are used, depending on whether
    # UNICODE was defined during compilation or not.
    def is_t_type(type):
        t_types_re = r'\b({})\b'.format('|'.join([
            'LPCTSTR',
            'PCTSTR',
            'LPTSTR',
            'PTSTR',
            'TBYTE',
            'PTBYTE',
            'TCHAR',
        ]))
        return re.search(t_types_re, type) is not None

    if is_t_type(func_info.ret_type):
        return False
    for param in func_info.params:
        if is_t_type(param.type_text):
            return False

    # Some functions look like declarations but are, in fact, just ordinary
    # sentences. We detect this heuristically by searching for declarations
    # that start with an uppercase letter and contain "the".
    return not re.fullmatch(r'[A-Z].*\bthe\b.*', func_info.decl)


def wrong_func_parameters(params):
    c = params.count('(')
    return (c != params.count(')')) or c > 10


def parse_all_enums(text, file):
    """Gets all enums from header, returns list of Enum objects."""
    enums = get_all_enums(text)
    return [parse_enum(enum, file) for enum in enums]


def parse_typedefs(text):
    """Parses typedefs from text except struct, union and enum typedefs.

    Parses them as function parameters - same syntax.
    """
    typedefs = get_typedefs(text)
    to_parse = []
    for t_def in typedefs:
        t_defs = split_params(t_def)
        if len(t_defs) == 0:
            continue
        to_parse.append(t_defs[0])
        if len(t_defs) > 1:
            t_type = re.search(r'^([\w\s]+)?(?=\s+(?:\*|\w+|\(\*))', t_defs[0])
            t_type = t_type[1] if t_type else ''
            to_parse.extend(f'{t_type} {next_type}' for next_type in t_defs[1:])
    parsed = []
    for t_def in to_parse:
        if t_def.endswith(')'):
            t_def = remove_brackets_around_pointer(t_def)
        parsed = parsed + parse_func_parameters(t_def)
    return parsed


def remove_brackets_around_pointer(ptr):
    """Remove brackets around typedef name when it's typedef to pointer,
    except pointer to function.
    'typedef int (*HANDLER);'
    """
    return re.sub(r'\((\s*\*\s*\w+)\)(;?)$', r'\1\2', ptr)


def get_typedefs(text):
    """Gets typedefs from text except struct, union and enum typedefs."""
    return re.findall('typedef\s*([\w\s\*\[\]\(\),.+-/]+?)\s*;', text)
