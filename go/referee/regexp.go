// SPDX-License-Identifier: Apache-2.0

package referee

import (
	"regexp"
	"strings"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

// Python's jsonschema matches a pattern with re.search, where \s is any
// Unicode whitespace and a closing $ also matches just before a final newline.
// Go's regexp (RE2) takes \s as ASCII only and $ as the end of the text, so a
// pattern is rewritten before it is compiled, or the same Result would be
// decided differently. The schema's patterns use only ^, $, \s, classes and
// counted repeats; anything else is compiled as written.
const (
	pyWhitespace = `\t-\r\x1c-\x1f\x85\p{Z}`
	pyEnd        = `\n?\z`
)

func pythonRegexp(pattern string) (jsonschema.Regexp, error) {
	var b strings.Builder
	inClass := false
	for i := 0; i < len(pattern); i++ {
		switch ch := pattern[i]; {
		case ch == '\\' && i+1 < len(pattern):
			if pattern[i+1] == 's' {
				if inClass {
					b.WriteString(pyWhitespace)
				} else {
					b.WriteString("[" + pyWhitespace + "]")
				}
			} else {
				b.WriteByte(ch)
				b.WriteByte(pattern[i+1])
			}
			i++
		case ch == '[' && !inClass:
			inClass = true
			b.WriteByte(ch)
		case ch == ']' && inClass:
			inClass = false
			b.WriteByte(ch)
		case ch == '$' && !inClass && i == len(pattern)-1:
			b.WriteString(pyEnd)
		default:
			b.WriteByte(ch)
		}
	}
	re, err := regexp.Compile(b.String())
	if err != nil {
		return nil, err
	}
	return re, nil
}
