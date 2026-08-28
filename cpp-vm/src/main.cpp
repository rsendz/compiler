// A C++17 implementation of the Little Duck address-based virtual machine.
//
// It intentionally consumes the exact IR format emitted by the Python
// compiler. Keeping the format stable lets the Python implementation remain a
// readable reference while this executable exercises the runtime boundary.

#include <algorithm>
#include <cctype>
#include <charconv>
#include <cmath>
#include <exception>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <variant>
#include <vector>

namespace {

constexpr int kEmptyField = -1;
constexpr int kRecursionLimit = 500;
using Value = std::variant<long long, double, std::string, bool>;

struct RuntimeError : std::exception {
    explicit RuntimeError(std::string message) : message(std::move(message)) {}

    const char* what() const noexcept override { return message.c_str(); }

    std::string message;
    int quad_number = 0;
    int source_line = 0;
};

struct Quad {
    std::string op;
    int left;
    int right;
    int result;
    int source_line;
};

struct Function {
    int start = 0;
    int parameters = 0;
    std::unordered_map<std::string, int> memory;
};

struct Program {
    std::unordered_map<int, Value> constants;
    std::unordered_map<std::string, int> global_counts;
    std::unordered_map<int, Function> functions_by_start;
    std::vector<Quad> quads;
};

struct Frame {
    std::unordered_map<int, Value> cells;
    std::unordered_map<std::string, int> counts;
};

const std::vector<std::pair<std::string, int>> kRegions = {
    {"global_int", 1000}, {"global_float", 2000}, {"global_str", 3000},
    {"global_bool", 4000}, {"global_void", 5000},
    {"local_int", 7000}, {"local_float", 8000}, {"local_str", 9000},
    {"local_bool", 10000},
    {"temp_int", 12000}, {"temp_float", 13000}, {"temp_str", 14000},
    {"temp_bool", 15000},
    {"cte_int", 17000}, {"cte_float", 18000}, {"cte_str", 19000},
    {"cte_bool", 20000},
};

std::string trim(std::string text) {
    const auto first = text.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) {
        return "";
    }
    const auto last = text.find_last_not_of(" \t\r\n");
    return text.substr(first, last - first + 1);
}

bool is_numeric_text(const std::string& text) {
    if (text.empty()) {
        return false;
    }
    return std::all_of(text.begin(), text.end(), [](unsigned char character) {
        return std::isdigit(character) || character == '-';
    });
}

std::vector<std::string> fields(const std::string& line) {
    std::istringstream stream(line);
    std::vector<std::string> result;
    for (std::string field; stream >> field;) {
        result.push_back(field);
    }
    return result;
}

std::string unescape(const std::string& text) {
    std::string output;
    for (std::size_t index = 0; index < text.size(); ++index) {
        if (text[index] == '\\' && index + 1 < text.size()) {
            const char escaped = text[++index];
            switch (escaped) {
                case 'n': output.push_back('\n'); break;
                case 't': output.push_back('\t'); break;
                case 'r': output.push_back('\r'); break;
                case '\\': output.push_back('\\'); break;
                case '"': output.push_back('"'); break;
                case '0': output.push_back('\0'); break;
                default: output.push_back('\\'); output.push_back(escaped); break;
            }
        } else {
            output.push_back(text[index]);
        }
    }
    return output;
}

Value parse_constant(const std::string& text) {
    if (text.size() >= 2 && text.front() == '"' && text.back() == '"') {
        return unescape(text.substr(1, text.size() - 2));
    }
    if (text == "true") {
        return true;
    }
    if (text == "false") {
        return false;
    }
    if (text.find('.') != std::string::npos) {
        return std::stod(text);
    }
    return std::stoll(text);
}

Program load_program(const std::string& path) {
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("Could not open the intermediate representation file '" + path + "'");
    }

    Program program;
    std::string section;
    std::optional<Function> function;
    for (std::string raw_line; std::getline(input, raw_line);) {
        const std::string line = trim(raw_line);
        if (line.empty()) {
            continue;
        }
        if (line == "const" || line == "global" || line == "funcs" || line == "quads") {
            section = line;
            continue;
        }
        if (section == "const") {
            const auto separator = line.find_last_of(" \t");
            if (separator == std::string::npos) {
                throw std::runtime_error("Malformed constant in IR");
            }
            const auto value = trim(line.substr(0, separator));
            const auto address = std::stoi(trim(line.substr(separator + 1)));
            program.constants[address] = parse_constant(value);
            continue;
        }

        const auto row = fields(line);
        if (section == "global") {
            if (row.size() == 2) {
                program.global_counts[row[0]] = std::stoi(row[1]);
            }
            continue;
        }
        if (section == "funcs") {
            if (row.empty()) {
                continue;
            }
            if (row[0] == "func") {
                function = Function{};
                function->start = std::stoi(row.at(2));
            } else if (row[0] == "params" && function) {
                function->parameters = std::stoi(row.at(1));
            } else if (row[0] == "endfunc" && function) {
                program.functions_by_start[function->start] = *function;
                function.reset();
            } else if (function && row.size() == 2) {
                function->memory[row[0]] = std::stoi(row[1]);
            }
            continue;
        }
        if (section == "quads" && row.size() >= 5 && is_numeric_text(row[0])) {
            program.quads.push_back({row[1], std::stoi(row[2]), std::stoi(row[3]),
                                     std::stoi(row[4]), row.size() > 5 ? std::stoi(row[5]) : 0});
        }
    }
    return program;
}

std::optional<std::pair<std::string, int>> region_for(int address) {
    for (const auto& [name, base] : kRegions) {
        if (address >= base && address < base + 1000) {
            return std::make_pair(name, base);
        }
    }
    return std::nullopt;
}

bool is_frame_region(const std::string& region) {
    return region.rfind("local_", 0) == 0 || region.rfind("temp_", 0) == 0;
}

class RuntimeMemory {
public:
    RuntimeMemory(const std::unordered_map<int, Value>& constants,
                  const std::unordered_map<std::string, int>& global_counts)
        : globals_(constants), global_counts_(global_counts) {}

    void push_frame(std::unordered_map<std::string, int> counts) {
        frames_.push_back({{}, std::move(counts)});
    }

    void pop_frame() {
        if (!frames_.empty()) {
            frames_.pop_back();
        }
    }

    void set_current(int address, Value value) { current_frame().cells[address] = std::move(value); }

    bool has_global(int address) const { return globals_.find(address) != globals_.end(); }

    Value read(int address) {
        auto& cells = cells_for(address);
        if (const auto found = cells.find(address); found != cells.end()) {
            return found->second;
        }
        const auto region = region_for(address);
        const std::string name = region ? region->first : "None";
        if (is_reserved(address)) {
            throw RuntimeError("read of uninitialized memory (address " + std::to_string(address) +
                               ", region " + name + "): a variable was used before being assigned a value");
        }
        throw RuntimeError("read of unreserved memory (address " + std::to_string(address) +
                           ", region " + name + ")");
    }

    void write(int address, Value value) { cells_for(address)[address] = std::move(value); }

private:
    Frame& current_frame() {
        if (frames_.empty()) {
            throw RuntimeError("access to local/temporary memory with no active frame");
        }
        return frames_.back();
    }

    std::unordered_map<int, Value>& cells_for(int address) {
        const auto region = region_for(address);
        if (region && is_frame_region(region->first)) {
            return current_frame().cells;
        }
        return globals_;
    }

    bool is_reserved(int address) const {
        const auto region = region_for(address);
        if (!region) {
            return false;
        }
        const auto& [name, base] = *region;
        const auto& counts = is_frame_region(name) && !frames_.empty()
            ? frames_.back().counts : global_counts_;
        const auto found = counts.find(name);
        const int reserved = found == counts.end() ? 0 : found->second;
        return address >= base && address < base + reserved;
    }

    std::unordered_map<int, Value> globals_;
    std::unordered_map<std::string, int> global_counts_;
    std::vector<Frame> frames_;
};

bool is_integer(const Value& value) { return std::holds_alternative<long long>(value); }
bool is_number(const Value& value) { return is_integer(value) || std::holds_alternative<double>(value); }

double as_double(const Value& value) {
    return is_integer(value) ? static_cast<double>(std::get<long long>(value)) : std::get<double>(value);
}

long long as_integer(const Value& value) { return std::get<long long>(value); }
bool as_bool(const Value& value) { return std::get<bool>(value); }

std::string format_value(const Value& value) {
    if (std::holds_alternative<bool>(value)) {
        return std::get<bool>(value) ? "true" : "false";
    }
    if (std::holds_alternative<long long>(value)) {
        return std::to_string(std::get<long long>(value));
    }
    if (std::holds_alternative<double>(value)) {
        char buffer[64];
        const auto [end, error] = std::to_chars(
            std::begin(buffer), std::end(buffer), std::get<double>(value),
            std::chars_format::general);
        std::string result;
        if (error == std::errc()) {
            result.assign(buffer, end);
        } else {
            std::ostringstream stream;
            stream << std::setprecision(16) << std::get<double>(value);
            result = stream.str();
        }
        if (result.find_first_of(".eE") == std::string::npos) {
            result += ".0";
        }
        return result;
    }
    return std::get<std::string>(value);
}

bool values_equal(const Value& left, const Value& right) {
    if (is_number(left) && is_number(right)) {
        return as_double(left) == as_double(right);
    }
    return left == right;
}

class VirtualMachine {
public:
    explicit VirtualMachine(Program program)
        : program_(std::move(program)), memory_(program_.constants, program_.global_counts) {}

    void run() {
        std::unordered_map<std::string, int> main_counts;
        for (const char* region : {"temp_int", "temp_float", "temp_str", "temp_bool"}) {
            main_counts[region] = program_.global_counts[region];
        }
        memory_.push_frame(std::move(main_counts));

        while (!halted_ && instruction_pointer_ < program_.quads.size()) {
            const Quad& quad = program_.quads[instruction_pointer_];
            try {
                const auto jump = execute(quad);
                instruction_pointer_ = jump ? static_cast<std::size_t>(*jump - 1) : instruction_pointer_ + 1;
            } catch (RuntimeError& error) {
                if (error.quad_number == 0) {
                    error.quad_number = static_cast<int>(instruction_pointer_) + 1;
                }
                if (error.source_line == 0) {
                    error.source_line = quad.source_line;
                }
                throw;
            }
        }
    }

    const std::string& output() const { return output_; }

private:
    std::optional<int> execute(const Quad& quad) {
        const auto read = [&](int address) { return memory_.read(address); };
        const auto binary = [&](const std::string& op) {
            const Value left = read(quad.left);
            const Value right = read(quad.right);
            if (op == "+") {
                memory_.write(quad.result, is_integer(left) && is_integer(right)
                    ? Value(as_integer(left) + as_integer(right)) : Value(as_double(left) + as_double(right)));
            } else if (op == "-") {
                memory_.write(quad.result, is_integer(left) && is_integer(right)
                    ? Value(as_integer(left) - as_integer(right)) : Value(as_double(left) - as_double(right)));
            } else if (op == "*") {
                memory_.write(quad.result, is_integer(left) && is_integer(right)
                    ? Value(as_integer(left) * as_integer(right)) : Value(as_double(left) * as_double(right)));
            } else if (op == ">" || op == "<" || op == ">=" || op == "<=") {
                const double lhs = as_double(left);
                const double rhs = as_double(right);
                const bool result = op == ">" ? lhs > rhs : op == "<" ? lhs < rhs :
                    op == ">=" ? lhs >= rhs : lhs <= rhs;
                memory_.write(quad.result, result);
            } else if (op == "==" || op == "!=") {
                const bool equal = values_equal(left, right);
                memory_.write(quad.result, op == "==" ? equal : !equal);
            }
        };

        if (quad.op == "+" || quad.op == "-" || quad.op == "*" || quad.op == ">" || quad.op == "<" ||
            quad.op == ">=" || quad.op == "<=" || quad.op == "==" || quad.op == "!=") {
            binary(quad.op);
        } else if (quad.op == "/") {
            const Value divisor = read(quad.right);
            if (as_double(divisor) == 0.0) {
                throw RuntimeError("division by zero");
            }
            memory_.write(quad.result, as_double(read(quad.left)) / as_double(divisor));
        } else if (quad.op == "u+" || quad.op == "u-") {
            const Value value = read(quad.left);
            if (is_integer(value)) {
                memory_.write(quad.result, quad.op == "u+" ? Value(as_integer(value)) : Value(-as_integer(value)));
            } else {
                memory_.write(quad.result, quad.op == "u+" ? Value(as_double(value)) : Value(-as_double(value)));
            }
        } else if (quad.op == "not") {
            memory_.write(quad.result, !as_bool(read(quad.left)));
        } else if (quad.op == "=") {
            memory_.write(quad.result, read(quad.left));
        } else if (quad.op == "gotomain" || quad.op == "goto") {
            return quad.result;
        } else if (quad.op == "gotof") {
            return !as_bool(read(quad.left)) ? std::optional<int>(quad.result) : std::nullopt;
        } else if (quad.op == "gotot") {
            return as_bool(read(quad.left)) ? std::optional<int>(quad.result) : std::nullopt;
        } else if (quad.op == "sub") {
            pending_params_.emplace_back();
        } else if (quad.op == "param") {
            pending_params_.back()[quad.result] = read(quad.left);
        } else if (quad.op == "gosub") {
            if (depth_ + 1 > kRecursionLimit) {
                throw RuntimeError("maximum recursion depth exceeded (" + std::to_string(kRecursionLimit) + ")");
            }
            auto function = program_.functions_by_start.find(quad.result);
            if (function == program_.functions_by_start.end()) {
                throw RuntimeError("unknown function entry " + std::to_string(quad.result));
            }
            auto arguments = std::move(pending_params_.back());
            pending_params_.pop_back();
            memory_.push_frame(function->second.memory);
            for (auto& [address, value] : arguments) {
                memory_.set_current(address, std::move(value));
            }
            return_addresses_.push_back(instruction_pointer_ + 1);
            result_slots_.push_back(quad.right);
            function_slots_.push_back(quad.left);
            ++depth_;
            return quad.result;
        } else if (quad.op == "return") {
            memory_.write(quad.result, read(quad.left));
        } else if (quad.op == "endfun") {
            memory_.pop_frame();
            --depth_;
            const std::size_t resume = return_addresses_.back();
            return_addresses_.pop_back();
            const int result_slot = result_slots_.back();
            result_slots_.pop_back();
            const int function_slot = function_slots_.back();
            function_slots_.pop_back();
            if (result_slot != kEmptyField && memory_.has_global(function_slot)) {
                memory_.write(result_slot, memory_.read(function_slot));
            }
            return static_cast<int>(resume + 1);
        } else if (quad.op == "ver") {
            const long long index = as_integer(read(quad.left));
            if (index < quad.right || index >= quad.result) {
                throw RuntimeError("array index " + std::to_string(index) + " is out of bounds (valid range is " +
                                   std::to_string(quad.right) + ".." + std::to_string(quad.result - 1) + ")");
            }
        } else if (quad.op == "arrayread") {
            memory_.write(quad.result, read(quad.left + static_cast<int>(as_integer(read(quad.right)))));
        } else if (quad.op == "arraywrite") {
            memory_.write(quad.result + static_cast<int>(as_integer(read(quad.right))), read(quad.left));
        } else if (quad.op == "print") {
            output_ += format_value(read(quad.left));
        } else if (quad.op == "newline") {
            output_ += '\n';
        } else if (quad.op == "end") {
            halted_ = true;
        } else {
            throw RuntimeError("unknown operator '" + quad.op + "'");
        }
        return std::nullopt;
    }

    Program program_;
    RuntimeMemory memory_;
    std::size_t instruction_pointer_ = 0;
    int depth_ = 1;
    bool halted_ = false;
    std::vector<std::unordered_map<int, Value>> pending_params_;
    std::vector<std::size_t> return_addresses_;
    std::vector<int> result_slots_;
    std::vector<int> function_slots_;
    std::string output_;
};

void write_output(const std::string& output, bool end_with_newline = false) {
    if (output.empty()) {
        return;
    }
    std::cout << output;
    if (end_with_newline && output.back() != '\n') {
        std::cout << '\n';
    }
}

std::string describe(const RuntimeError& error) {
    if (error.source_line != 0) {
        return "Runtime error at line " + std::to_string(error.source_line) + " (quadruple " +
            std::to_string(error.quad_number) + "): " + error.message;
    }
    return "Runtime error (quadruple " + std::to_string(error.quad_number) + "): " + error.message;
}

}  // namespace

int main(int argc, char* argv[]) {
    const std::string path = argc > 1 ? argv[1] : "ir-addresses.txt";
    try {
        VirtualMachine machine(load_program(path));
        try {
            machine.run();
        } catch (const RuntimeError& error) {
            write_output(machine.output(), true);
            std::cout << describe(error) << '\n';
            return 1;
        }
        write_output(machine.output());
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
