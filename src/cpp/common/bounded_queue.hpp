#pragma once
#include <algorithm>

#include <condition_variable>
#include <cstddef>
#include <mutex>
#include <optional>
#include <queue>
#include <utility>

namespace sensing {

template<typename T>
class BoundedQueue {
public:
    explicit BoundedQueue(
        const std::size_t capacity
    )
        : capacity_(capacity) {
    }

    BoundedQueue(const BoundedQueue&) = delete;
    BoundedQueue& operator=(const BoundedQueue&) = delete;

    bool push(T value) {
        std::unique_lock lock{mutex_};

        not_full_.wait(
            lock,
            [this] {
                return closed_
                    || queue_.size() < capacity_;
            }
        );

        if (closed_) {
            return false;
        }

        queue_.push(std::move(value));
        high_water_mark_ = std::max(
            high_water_mark_,
            queue_.size()
        );

        not_empty_.notify_one();
        return true;
    }

    std::optional<T> pop() {
        std::unique_lock lock{mutex_};

        not_empty_.wait(
            lock,
            [this] {
                return closed_ || !queue_.empty();
            }
        );

        if (queue_.empty()) {
            return std::nullopt;
        }

        T value = std::move(queue_.front());
        queue_.pop();

        not_full_.notify_one();
        return value;
    }

    void close() {
        {
            std::lock_guard lock{mutex_};
            closed_ = true;
        }

        not_empty_.notify_all();
        not_full_.notify_all();
    }

    [[nodiscard]]
    std::size_t size() const {
        std::lock_guard lock{mutex_};
        return queue_.size();
    }

    [[nodiscard]]
    std::size_t high_water_mark() const {
        std::lock_guard lock{mutex_};
        return high_water_mark_;
    }

private:
    const std::size_t capacity_;

    mutable std::mutex mutex_;
    std::condition_variable not_empty_;
    std::condition_variable not_full_;

    std::queue<T> queue_;

    bool closed_{false};
    std::size_t high_water_mark_{0};
};

}  // namespace sensing
